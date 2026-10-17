"""Agent readiness: the 12.1 table per link and the 12.2 overall state (plan 12; G-01).

``assess_agent`` loads the agent as a run would (owner fast path, else someone else's agent
under the caller's ``agent#can_run``: 404 hidden, 403 visible only), derives the chat run
identity (11.1) and asks ``ReadinessPlanner`` — the F-06 decisions without raising — then
checks each link's state and credentials without decrypting anything. Subagents are one
hop: the child's own items follow its link (required only when both are).

12.2 overall: ``blocked`` when a required item is ``no_permission``,
``owner_must_connect`` or ``unavailable``; ``needs_connection`` when a required item is
``connect_by_me`` and nothing else required is wrong; ``limited`` when only optional items
are wrong; else ``ready``. The LLM model is always required.

12.3 visibility (``present``, per item's own agent): a caller without that agent's
``agent#can_view_config`` sees the items
it must act on (``connect_by_me`` with the resource id, ``owner_must_connect`` with the
service name only); every other item is a count per (kind, required, result) without ids
or names.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.identity import (
    AgentRunIdentity,
    AgentRunSource,
    derive_child_agent_run_identity,
    make_agent_runtime_name,
    resolve_agent_run_identity,
)
from app.authz.agent_access import agents_permissions
from app.authz.org_context import OrgContext
from app.authz.readiness_verdicts import ReadinessPlanner
from app.authz.service import AuthzService
from app.error_codes import agent_not_found
from app.exceptions import AppError
from app.models.agent import Agent
from app.services import readiness_cache
from app.services.chat.run_agents import get_run_agent_with_tools
from app.services.readiness_items import BLOCKING, LinkAssessor, ReadinessItem, action_for


@dataclass(frozen=True, slots=True)
class Readiness:
    agent: Agent
    items: tuple[ReadinessItem, ...]
    # Agents (root and one-hop children) whose configuration the caller may view.
    visible: frozenset[UUID] = frozenset()

    @property
    def can_view_config(self) -> bool:
        return self.agent.id in self.visible

    @property
    def overall(self) -> str:
        return overall_of(self.items)

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(item.result for item in self.items))


def overall_of(items: tuple[ReadinessItem, ...] | list[ReadinessItem]) -> str:
    required = {item.result for item in items if item.required}
    if required & BLOCKING:
        return "blocked"
    if "connect_by_me" in required:
        return "needs_connection"
    if any(item.result != "ready" for item in items):
        return "limited"
    return "ready"


def chat_identity(agent: Agent, context: OrgContext) -> AgentRunIdentity:
    return resolve_agent_run_identity(
        agent_id=agent.id,
        agent_owner_user_id=agent.user_id,
        runtime_name=agent.runtime_name or make_agent_runtime_name(agent.id),
        identity_mode=agent.identity_mode,
        source=AgentRunSource.CHAT,
        caller_user_id=context.user_id,
    )


async def agent_items(
    db: AsyncSession,
    authz: AuthzService,
    context: OrgContext,
    agent: Agent,
    identity: AgentRunIdentity,
    *,
    root: bool = True,
) -> list[ReadinessItem]:
    verdicts = await ReadinessPlanner(context, identity, authz).verdicts(agent, root=root)
    assess = LinkAssessor(db, authz, agent, verdicts)
    items = [await assess.model()]
    items += [await assess.tool(link) for link in agent.tool_links]
    items += await assess.mcp_servers()
    items += [assess.skill(link) for link in agent.skill_links]
    if not root:
        return items  # runs are one hop deep
    for link in agent.sub_agent_links:
        item = assess.sub_agent(link)
        items.append(item)
        if item.result != "ready":
            continue
        child = link.sub_agent
        child_identity = derive_child_agent_run_identity(identity, child)
        for child_item in await agent_items(db, authz, context, child, child_identity, root=False):
            child_item.required = child_item.required and item.required
            items.append(child_item)
    return items


async def config_viewable(
    authz: AuthzService, context: OrgContext, agent: Agent
) -> frozenset[UUID]:
    """``agent#can_view_config`` for the root and its one-hop children, one BatchCheck."""
    tree = {agent.id: agent.user_id}
    tree |= {
        link.sub_agent.id: link.sub_agent.user_id
        for link in agent.sub_agent_links
        if link.sub_agent is not None
    }
    rows = [(agent_id, owner == context.user_id) for agent_id, owner in tree.items()]
    perms = await agents_permissions(authz, context, rows)
    return frozenset(
        agent_id
        for agent_id, owned in rows
        if owned or "can_view_config" in perms.get(agent_id, [])
    )


async def assess_agent(
    db: AsyncSession,
    authz: AuthzService,
    context: OrgContext,
    agent_id: UUID,
    *,
    with_visibility: bool = True,
) -> Readiness:
    """``with_visibility=False`` (summaries): skip the ``can_view_config`` checks."""
    agent = await get_run_agent_with_tools(db, agent_id, context)
    if agent is None:
        raise agent_not_found()
    items = await agent_items(db, authz, context, agent, chat_identity(agent, context))
    visible = await config_viewable(authz, context, agent) if with_visibility else frozenset()
    return Readiness(agent, tuple(items), visible)


def _shown(item: ReadinessItem, *, full: bool) -> dict[str, Any]:
    body: dict[str, Any] = {"kind": item.kind, "required": item.required, "result": item.result}
    if full or item.result == "connect_by_me":
        body["id"] = str(item.id) if item.id is not None else None
    if full:
        body["agent_id"] = str(item.agent_id)
    body["name"] = item.name
    if item.credential is not None and (full or item.result == "connect_by_me"):
        body["credential"] = item.credential
    body["action"] = item.action
    return body


def present(readiness: Readiness) -> dict[str, Any]:
    """The 12.3 body (also the ``409 agent_not_ready`` details for G-02). Each item is shown
    by its own agent's ``can_view_config`` (a child's settings may be hidden)."""
    items: list[dict[str, Any]] = []
    counted: Counter[tuple[str, bool, str]] = Counter()
    for item in readiness.items:
        full = item.agent_id in readiness.visible
        if full or item.result in ("connect_by_me", "owner_must_connect"):
            items.append(_shown(item, full=full))
        else:
            counted[(item.kind, item.required, item.result)] += 1
    for (kind, required, result), count in counted.items():
        action = action_for(result, None)
        items.append(
            {"kind": kind, "count": count, "required": required, "result": result}
            | ({"action": action} if action else {})
        )
    return {
        "overall": readiness.overall,
        "identity_mode": readiness.agent.identity_mode,
        "items": items,
    }


def summary(readiness: Readiness) -> dict[str, Any]:
    return {"overall": readiness.overall, "counts": readiness.counts}


async def summaries(
    db: AsyncSession, authz: AuthzService, context: OrgContext, agent_ids: list[UUID]
) -> dict[str, dict[str, Any]]:
    """Cached per (organization, caller, agent); agents the caller may not run are left
    out (no 404/403 oracle per id). ``authz_unavailable`` still fails the request."""
    out: dict[str, dict[str, Any]] = {}
    for agent_id in dict.fromkeys(agent_ids):
        key = (context.tenant_id, context.org_id, context.user_id, agent_id)
        cached = readiness_cache.get(key)
        if cached is None:
            try:
                readiness = await assess_agent(db, authz, context, agent_id, with_visibility=False)
                cached = summary(readiness)
            except AppError as error:
                if error.status not in (403, 404):
                    raise
                cached = readiness_cache.HIDDEN  # cached too: same answer, same cost
            readiness_cache.put(key, cached)
        if cached is not readiness_cache.HIDDEN:
            out[str(agent_id)] = cached
    return out


__all__ = [
    "Readiness",
    "agent_items",
    "assess_agent",
    "chat_identity",
    "overall_of",
    "present",
    "summaries",
    "summary",
]
