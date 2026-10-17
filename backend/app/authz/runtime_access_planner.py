"""Per-link run decisions inside ``prepare_authorized_runtime`` (plan 11.0, 11.1, 11.2; F-06).

``RuntimeAccessPlanner.plan(agent)`` turns a loaded agent into the links a run may bind:

* Nothing enforced (``legacy``, ``shadow`` or none of the run types in
  ``AUTHZ_ENFORCE_TYPES``): the existing owner check (``require_agent_capabilities``, the
  one-hop tree for root runs) and every link — byte-identical, no query.
* Enforced: the root of someone else's agent needs the caller's ``agent#can_run``
  (``require_runnable_agent``); optional broken links (F-03) are skipped; a link to a row the run
  account owns needs nothing (owner fast path); platform rows are decided as the run
  account (catalog fanout, ``organization#member`` consumer); any other row as the 11.1
  subject (``agent:A`` for fixed runs, ``user:caller`` for caller-identity runs) with
  ``can_use`` (tool, skill), ``can_invoke`` (MCP tool) or ``can_run`` (subagent), plus
  the model's ``llm_model#can_use`` — one BatchCheck per principal through the run
  decision memo (``run_decisions``: decided before a lease's locks, reused under them). A denied
  required link (broken included, or the model, or a non-owned row of a type not yet
  enforced) is ``409 agent_not_ready`` (F-03 item shape, no ids); a denied optional link is left out
  (``excluded``; serialized views carry no ids). Credentials stay with
  ``RunCredentialResolver``, which skips the decisions made here (``decided``).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from app.agent_runtime.identity import AgentRunIdentity
from app.authz import denials
from app.authz.attach_candidates import ATTACH_PERMISSIONS
from app.authz.client import AuthzUnavailable
from app.authz.conversation_agent_checks import current_authz
from app.authz.error_codes import authorization_error
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.run_agent_scope import require_runnable_agent
from app.authz.run_decisions import batch_decide
from app.authz.runtime_capability_scope import (
    require_agent_capabilities,
    require_runtime_capability_tree,
)
from app.authz.service import AuthzService
from app.credentials.run_subject import RunSubject, subject_for_agent
from app.models.agent import Agent

RUN_TYPES = ("agent", "tool", "mcp_tool", "skill", "llm_model")
Reason = Literal["broken", "denied"]


@dataclass(frozen=True, slots=True)
class Exclusion:
    kind: str
    resource_id: UUID
    reason: Reason

    def as_dict(self) -> dict[str, str]:
        """Serializable view without the resource id: a runner may not see the row."""
        return {"type": self.kind, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class RuntimeAccessPlan:
    agent: Agent
    subject: RunSubject
    tool_links: tuple[Any, ...]
    mcp_tool_links: tuple[Any, ...]
    skill_links: tuple[Any, ...]
    sub_agent_links: tuple[Any, ...]
    excluded: tuple[Exclusion, ...] = ()
    # FGA objects confirmed here (``RunCredentialResolver`` does not ask them again).
    decided: frozenset[str] = frozenset()

    @property
    def excluded_capabilities(self) -> list[dict[str, str]]:
        return [item.as_dict() for item in self.excluded]

    @property
    def excluded_counts(self) -> dict[str, int]:
        return _counts(item.kind for item in self.excluded)


def _platform(row: Any) -> bool:
    return bool(getattr(row, "is_system", False)) and row.org_id is None and row.tenant_id is None


@dataclass(slots=True)
class _Pending:
    kind: str
    link: Any
    obj: str
    principal: Principal
    required: bool
    implied: tuple[str, ...] = ()
    never: bool = False  # never runnable, never asked (foreign, broken, type not enforced)
    reason: Reason = "denied"


@dataclass(slots=True)
class _Draft:
    kept: dict[str, list[Any]] = field(default_factory=lambda: defaultdict(list))
    pending: list[_Pending] = field(default_factory=list)
    excluded: list[Exclusion] = field(default_factory=list)


_LINKS = (
    ("tool", "tool_links", "tool"),
    ("mcp_tool", "mcp_tool_links", "mcp_tool"),
    ("skill", "skill_links", "skill"),
    ("agent", "sub_agent_links", "sub_agent"),
)


def runtime_access_enforced(authz: AuthzService | None = None) -> bool:
    service = authz or current_authz()
    return any(service.enforces(kind) for kind in RUN_TYPES)


def require_legacy_runtime_tree(agent: Agent, context: OrgContext) -> None:
    """The existing owner check of a run's one-hop tree while nothing is enforced; once
    enforced, ``prepare_authorized_runtime`` decides the tree instead."""
    if not runtime_access_enforced():
        require_runtime_capability_tree(agent, context)


class RuntimeAccessPlanner:
    def __init__(
        self,
        context: OrgContext,
        identity: AgentRunIdentity | None,
        authz: AuthzService | None = None,
    ) -> None:
        self.context, self.identity = context, identity
        self.authz = authz or current_authz()

    def enforced(self) -> bool:
        return runtime_access_enforced(self.authz)

    async def plan(self, agent: Agent, *, root: bool = True) -> RuntimeAccessPlan:
        """``root``: decide the caller's ``agent#can_run`` (the legacy check covers the
        one-hop tree); a child (``root=False``) was decided by its parent's plan."""
        subject = subject_for_agent(agent, self.context, self.identity)
        if not self.enforced():
            check = require_runtime_capability_tree if root else require_agent_capabilities
            check(agent, self.context)
            tools, mcp, skills, subs = (tuple(_links(agent, n, root)) for _, n, _ in _LINKS)
            return RuntimeAccessPlan(agent, subject, tools, mcp, skills, subs)
        draft, allowed = await self._assess(agent, subject, root)
        blocked = [item for item in draft.pending if item.obj not in allowed]
        if any(item.required for item in blocked):
            # F-03's shape ({overall, items}); items carry no resource id (L2).
            items = [_blocked_item(agent, item) for item in blocked]
            raise authorization_error("agent_not_ready", {"overall": "blocked", "items": items})
        decided: set[str] = set()
        for item in draft.pending:
            if item.obj in allowed:
                decided.update((item.obj, *item.implied))
                if item.link is not None:
                    draft.kept[item.kind].append(item.link)
            elif item.link is not None:
                rid = UUID(item.obj.partition(":")[2])
                draft.excluded.append(Exclusion(item.kind, rid, item.reason))
        tools, mcp, skills, subs = (
            _ordered(_links(agent, name, root), draft.kept[kind]) for kind, name, _ in _LINKS
        )
        return RuntimeAccessPlan(
            agent, subject, tools, mcp, skills, subs, tuple(draft.excluded), frozenset(decided)
        )

    async def _assess(
        self, agent: Agent, subject: RunSubject, root: bool
    ) -> tuple[_Draft, set[str]]:
        """Every link classified and the pending ones decided (no raise for denials)."""
        if root:
            await require_runnable_agent(agent, self.context, self.identity, self.authz)
        draft = _Draft()
        run_source = subject.principal.run_source
        account = Principal("user", subject.account_user_id, self.context.org_id, run_source)
        for kind, name, attr in _LINKS:
            for link in _links(agent, name, root):
                self._classify(draft, kind, link, getattr(link, attr), subject, account)
        if agent.model is not None and self.authz.enforces("llm_model"):
            draft.pending.append(
                _Pending("llm_model", None, f"llm_model:{agent.model.id}", account, True)
            )
        return draft, await self._decide(draft.pending)

    def _classify(
        self,
        draft: _Draft,
        kind: str,
        link: Any,
        row: Any,
        subject: RunSubject,
        account: Principal,
    ) -> None:
        required = bool(getattr(link, "required", False))
        if getattr(link, "link_status", "active") == "broken":
            obj = f"{kind}:{_link_target(link)}"
            pending = _Pending(kind, link, obj, account, required, never=True, reason="broken")
            draft.pending.append(pending)
            return
        holder = row.server if kind == "mcp_tool" and row is not None else row
        if holder is None or not getattr(row, "enabled", True):
            draft.kept[kind].append(link)  # downstream skips missing/disabled rows as before
            return
        ctx, enforced = self.context, self.authz.enforces(kind)
        implied = (f"mcp_server:{holder.id}",) if kind == "mcp_tool" else ()
        if _platform(holder):
            if not enforced:
                draft.kept[kind].append(link)
                return
            principal, foreign = account, False
        elif (holder.org_id, holder.tenant_id) != (ctx.org_id, ctx.tenant_id):
            principal, foreign = account, True
        elif holder.user_id == subject.account_user_id:
            draft.kept[kind].append(link)  # owner fast path: the run account's own row
            return
        elif not enforced:
            # Partial enforcement: the type keeps the owner answer, so the run is not ready.
            principal, foreign, required = account, True, True
        else:
            principal, foreign = subject.principal, False
        obj = f"{kind}:{row.id}"
        draft.pending.append(_Pending(kind, link, obj, principal, required, implied, never=foreign))

    async def _decide(self, pending: list[_Pending]) -> set[str]:
        by_principal: dict[Principal, list[tuple[str, str]]] = defaultdict(list)
        for item in pending:
            if not item.never:
                permission = ATTACH_PERMISSIONS.get(item.kind, "can_use")
                by_principal[item.principal].append((permission, item.obj))
        allowed: set[str] = set()
        for principal, items in by_principal.items():
            try:
                answers = await batch_decide(self.authz, principal, items)
            except AuthzUnavailable as error:
                raise denials.authz_unavailable() from error
            allowed.update(obj for (_, obj), ok in answers.items() if ok)
        return allowed


def _blocked_item(agent: Agent, item: _Pending) -> dict[str, object]:
    broken = item.reason == "broken"
    return {
        "kind": item.kind,
        "agent_id": str(agent.id),
        "required": item.required,
        "result": "delegation_broken" if broken else "permission_denied",
        "reason": item.reason,
    }


def _counts(kinds: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for kind in kinds:
        counts[kind] += 1
    return dict(counts)


def _links(agent: Agent, name: str, root: bool) -> list[Any]:
    """Runs are one hop deep: a child's own subagent links are never loaded or bound."""
    return [] if name == "sub_agent_links" and not root else list(getattr(agent, name))


def _link_target(link: Any) -> UUID:
    for name in ("tool_id", "mcp_tool_id", "skill_id", "sub_agent_id"):
        value = getattr(link, name, None)
        if value is not None:
            return value
    raise ValueError("unknown link shape")


def _ordered(links: list[Any], kept: list[Any]) -> tuple[Any, ...]:
    keep = {id(link) for link in kept}
    return tuple(link for link in links if id(link) in keep)


__all__ = [
    "Exclusion",
    "RuntimeAccessPlan",
    "RuntimeAccessPlanner",
    "require_legacy_runtime_tree",
    "runtime_access_enforced",
]
