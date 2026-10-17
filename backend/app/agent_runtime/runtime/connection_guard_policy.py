"""What ``ConnectionGuardMiddleware`` checks for one run (plan 11.2, 11.4; F-07).

Built once per agent graph from the run's ``RuntimeAccessPlan`` (F-06) and the bound
LangChain tools — no secret values:

* ``denied``: names of the tools whose links the plan excluded (``permission_required``),
* ``guarded``: every bound tool that carries a credential, with the ids the guard needs
  to re-read the credential's status before the call and to build the 11.5 item,
* ``checks``: the FGA decision re-made before each credentialed call on a row the
  run account does not own (the plan decided it once; 11.4 "자격 증명이 실리는 호출은
  항상 새로 판정").

The guard is installed (``connection_guard.with_connection_guard``) only while run types
are enforced; legacy and shadow runs keep their graph byte-identical.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.tools import BaseTool
from sqlalchemy import select

from app.authz.attach_candidates import ATTACH_PERMISSIONS
from app.authz.client import AuthzUnavailable
from app.authz.conversation_agent_checks import current_authz
from app.authz.principal import Principal
from app.authz.runtime_access_planner import RuntimeAccessPlan
from app.authz.service import AuthzService
from app.database import SessionTenant, async_session, tenant_session
from app.models.credential import Credential
from app.models.resource_credential_binding import ResourceCredentialBinding as Binding

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GuardedTool:
    name: str
    resource_type: str  # "tool" | "mcp_server"
    resource_name: str
    credential_id: str
    credential_mode: str  # "shared" | "per_user"
    tenant_id: str | None
    resource_id: str | None = None  # tool / MCP server id (per_user binding re-check)
    account_user_id: str | None = None  # whose connection a per_user call uses


@dataclass(frozen=True, slots=True)
class CredentialState:
    status: str
    user_id: str | None
    definition_key: str
    bound: bool | None = None  # per_user: the account's binding still points at it


GuardMode = Literal["interrupt", "raise", "refuse"]


StateReader = Callable[[Sequence[GuardedTool]], Awaitable[Mapping[str, CredentialState]]]
Recheck = Callable[[], Awaitable[bool | None]]  # True allowed, False denied, None unavailable


def _binding_key(tool: GuardedTool) -> tuple[str, str, str, str | None]:
    return (tool.credential_id, tool.resource_type, str(tool.resource_id), tool.account_user_id)


async def read_credential_states(tools: Sequence[GuardedTool]) -> dict[str, CredentialState]:
    """Current status of the tools' credentials (one tenant session per company).

    A credential the session cannot see (no tenant, row gone, RLS) is simply absent:
    the guard then skips its pre-check and the leaf call fails closed.
    """

    by_tenant: dict[str, list[GuardedTool]] = defaultdict(list)
    for tool in tools:
        if tool.tenant_id:
            by_tenant[tool.tenant_id].append(tool)
    states: dict[str, CredentialState] = {}
    for tenant_id, group in by_tenant.items():
        ids = {uuid.UUID(tool.credential_id) for tool in group}
        per_user = {uuid.UUID(t.credential_id) for t in group if t.credential_mode == "per_user"}
        async with tenant_session(SessionTenant(uuid.UUID(tenant_id)), maker=async_session) as db:
            rows = await db.execute(
                select(
                    Credential.id, Credential.status, Credential.user_id, Credential.definition_key
                ).where(Credential.id.in_(ids))
            )
            found = {str(cid): (status, uid, key) for cid, status, uid, key in rows}
            bindings: set[tuple[str, str, str, str | None]] = set()
            if per_user:
                bound = await db.execute(
                    select(
                        Binding.credential_id,
                        Binding.resource_type,
                        Binding.resource_id,
                        Binding.user_id,
                    ).where(Binding.credential_id.in_(per_user))
                )
                bindings = {(str(c), t, str(r), str(u)) for c, t, r, u in bound}
        for tool in group:
            if tool.credential_id not in found:
                continue
            status, uid, key = found[tool.credential_id]
            bound_now = (
                _binding_key(tool) in bindings if tool.credential_mode == "per_user" else None
            )
            states[tool.credential_id] = CredentialState(
                status, str(uid) if uid else None, key, bound_now
            )
    return states


@dataclass(frozen=True, slots=True)
class ConnectionGuardPolicy:
    # interrupt: the user-facing root run; raise: triggers/invoke API (run ends blocked);
    # refuse: subagent graphs (typed refusals only, never an interrupt or a raise).
    mode: GuardMode
    denied: Mapping[str, str] = field(default_factory=dict)
    guarded: Mapping[str, GuardedTool] = field(default_factory=dict)
    checks: Mapping[str, Recheck] = field(default_factory=dict)
    actor_user_id: str | None = None
    read_states: StateReader = read_credential_states

    @property
    def interactive(self) -> bool:
        return self.mode == "interrupt"


def _safe_name(raw: str, fallback: str) -> str:
    from app.agent_runtime.tool_factory import _safe_tool_name

    return _safe_tool_name(raw, fallback=fallback)


def _config_for(tool: BaseTool, configs: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    auth = getattr(tool, "_auth_context", None)
    if auth is not None:  # MCPToolWithRetry (F-12): match server + original MCP name
        original = getattr(getattr(tool, "_original_tool", None), "name", tool.name)
        for config in configs:
            name = config.get("mcp_tool_name", config.get("name"))
            if config.get("mcp_server_id") == auth.server_id and name in (original, tool.name):
                return config
        return None
    for config in configs:
        if config.get("mcp_server_url"):
            continue
        fallback = str(config.get("definition_key") or "tool")
        if _safe_name(str(config.get("name") or ""), fallback) == tool.name:
            return config
    return None


def _guarded(tool: BaseTool, config: Mapping[str, Any]) -> GuardedTool | None:
    if not config.get("credential_id"):
        return None
    mcp = bool(config.get("mcp_server_url"))
    return GuardedTool(
        name=tool.name,
        resource_type="mcp_server" if mcp else "tool",
        resource_name=str(config.get("mcp_server_name") or config.get("name") or tool.name),
        credential_id=str(config["credential_id"]),
        credential_mode="per_user" if config.get("credential_mode") == "per_user" else "shared",
        tenant_id=str(config["tenant_id"]) if config.get("tenant_id") else None,
        resource_id=config.get("mcp_server_id") if mcp else config.get("tool_id"),
        account_user_id=config.get("credential_subject_user_id"),
    )


def _denied_names(plan: RuntimeAccessPlan) -> dict[str, str]:
    excluded = {(item.kind, item.resource_id) for item in plan.excluded}
    names: dict[str, str] = {}
    agent = plan.agent
    for link in getattr(agent, "tool_links", ()):
        row = link.tool
        if row is not None and ("tool", row.id) in excluded:
            names[row.name] = names[_safe_name(row.name, row.definition_key)] = (
                "permission_required"
            )
    for link in getattr(agent, "mcp_tool_links", ()):
        row = link.mcp_tool
        if row is not None and ("mcp_tool", row.id) in excluded:
            names[row.name] = "permission_required"
    return names


def _recheck(authz: AuthzService, principal: Principal, kind: str, obj: str) -> Recheck:
    permission = ATTACH_PERMISSIONS.get(kind, "can_use")

    async def check() -> bool | None:
        try:  # a fresh decision, never the run memo (11.4)
            answers = await authz.batch_check(principal, [(permission, obj)])
        except AuthzUnavailable:
            return None
        return bool(answers.get((permission, obj)))

    return check


def _checks(plan: RuntimeAccessPlan, matched: Mapping[str, Mapping[str, Any]]) -> dict:
    authz, subject = current_authz(), plan.subject
    checks: dict[str, Recheck] = {}
    for name, config in matched.items():
        mcp_id = config.get("mcp_tool_id")
        kind, rid = ("mcp_tool", mcp_id) if mcp_id else ("tool", config.get("tool_id"))
        obj = f"{kind}:{rid}"
        if rid and obj in plan.decided:
            checks[name] = _recheck(authz, subject.principal, kind, obj)
    return checks


def build_connection_guard_policy(
    cfg: Any, tools: Sequence[BaseTool], *, mode: GuardMode
) -> ConnectionGuardPolicy:
    configs = list(cfg.tools_config or ())
    guarded: dict[str, GuardedTool] = {}
    matched: dict[str, Mapping[str, Any]] = {}
    for tool in tools:
        config = _config_for(tool, configs)
        item = _guarded(tool, config) if config is not None else None
        if item is not None and config is not None:
            guarded[tool.name], matched[tool.name] = item, config
    plan: RuntimeAccessPlan | None = cfg.access_plan
    return ConnectionGuardPolicy(
        mode=mode,
        denied=_denied_names(plan) if plan is not None else {},
        guarded=guarded,
        checks=_checks(plan, matched) if plan is not None else {},
        actor_user_id=cfg.caller_user_id or cfg.agent_owner_user_id or cfg.user_id,
    )


__all__ = [
    "ConnectionGuardPolicy",
    "CredentialState",
    "GuardMode",
    "GuardedTool",
    "build_connection_guard_policy",
    "read_credential_states",
]
