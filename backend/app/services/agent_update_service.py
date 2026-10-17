"""Agent mutation service (split from ``agent_service``)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.identity import validate_identity_mode
from app.agent_runtime.runtime_policy import runtime_policy_to_json
from app.authz.agent_delegations import added_keys, lock_delegations, sync_agent_delegations
from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.attach_rules import (
    attach_baseline,
    audit_delegation_confirm,
    require_attachable,
    require_shared_attach,
)
from app.authz.broken_links import assert_no_runtime_view
from app.authz.org_context import OrgContext
from app.authz.organization_execution_prefix import mark_org_writer
from app.error_codes import agent_not_found
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.mcp_tool import AgentMcpToolLink
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink
from app.schemas.agent import AgentUpdate
from app.services.agent_binding_service import Reattach, _validate_agent_binding_ids, relink
from app.services.agent_query_service import get_agent


async def toggle_favorite(db: AsyncSession, agent: Agent, context: OrgContext) -> Agent:
    scoped = await get_agent(db, agent.id, context)
    if scoped is None:
        raise agent_not_found()
    agent = scoped
    agent.is_favorite = not agent.is_favorite
    await db.flush()
    await db.refresh(
        agent,
        ["model", "tool_links", "mcp_tool_links", "skill_links", "sub_agent_links"],
    )
    return agent


async def update_agent(
    db: AsyncSession, agent: Agent, data: AgentUpdate, *, context: OrgContext
) -> Agent:
    assert_no_runtime_view(db)  # F-03: a run-prep session hides broken links
    if any(
        v is not None
        for v in (data.tool_ids, data.mcp_tool_ids, data.skill_ids, data.sub_agent_ids)
    ):
        mark_org_writer(db)  # F-02: the prefix takes the org row FOR UPDATE (no upgrade)
    await load_agent_tree_for_execution(db, AgentExecutionTarget(agent.id, context))
    scoped = await get_agent(db, agent.id, context)
    if scoped is None:
        raise agent_not_found()
    agent = scoped
    if data.sub_agent_ids is not None and agent.id in data.sub_agent_ids:
        raise HTTPException(status_code=400, detail="Cannot add self as sub-agent")
    heal = Reattach(context.user_id, frozenset(data.reattach_ids or ()))  # F-03 (10.6)
    await _validate_agent_binding_ids(db, _without_kept_broken(data, agent, heal), context)
    # D17 (E-06): added account resources need can_share; a shared agent re-confirms.
    await require_attachable(
        db,
        context,
        tool_ids=_added(data.tool_ids, _linked(agent.tool_links, "tool_id", heal)),
        mcp_tool_ids=_added(data.mcp_tool_ids, _linked(agent.mcp_tool_links, "mcp_tool_id", heal)),
    )
    relinks = (data.tool_ids, data.mcp_tool_ids, data.skill_ids, data.sub_agent_ids)
    before = await attach_baseline(db, agent.id) if any(v is not None for v in relinks) else None
    if before is not None:  # E-02 order: org row before the agent/link/grant writes
        await lock_delegations(db, context)
    added = await added_keys(
        db,
        tool_ids=_added(data.tool_ids, _linked(agent.tool_links, "tool_id", heal)),
        mcp_tool_ids=_added(data.mcp_tool_ids, _linked(agent.mcp_tool_links, "mcp_tool_id", heal)),
        skill_ids=_added(data.skill_ids, _linked(agent.skill_links, "skill_id", heal)),
        agent_ids=_added(data.sub_agent_ids, _linked(agent.sub_agent_links, "sub_agent_id", heal)),
    )
    actor = context.user_id  # F-02: delegator of the link rows created below
    if data.identity_mode is not None and data.identity_mode != agent.identity_mode:
        next_mode = validate_identity_mode(data.identity_mode)
        if next_mode == "per_user" and await _count_active_triggers(db, agent.id) > 0:
            raise HTTPException(
                status_code=422,
                detail="per_user identity cannot be enabled while active triggers exist",
            )
        agent.identity_mode = next_mode
    if data.name is not None:
        agent.name = data.name
    if data.description is not None:
        agent.description = data.description
    if data.system_prompt is not None:
        agent.system_prompt = data.system_prompt
    if data.model_id is not None:
        agent.model_id = data.model_id
    if data.is_favorite is not None:
        agent.is_favorite = data.is_favorite
    if data.model_params is not None:
        agent.model_params = data.model_params
    if data.middleware_configs is not None:
        agent.middleware_configs = [mc.model_dump() for mc in data.middleware_configs]
    if "runtime_policy" in data.model_fields_set:
        agent.runtime_policy = (
            runtime_policy_to_json(data.runtime_policy) if data.runtime_policy is not None else None
        )
    if data.opener_questions is not None:
        agent.opener_questions = data.opener_questions
    if data.model_fallback_ids is not None:
        agent.model_fallback_list = (
            [str(fid) for fid in data.model_fallback_ids] if data.model_fallback_ids else None
        )
    if data.tool_ids is not None:
        agent.tool_links = relink(
            agent.tool_links,
            data.tool_ids,
            lambda link: link.tool_id,
            lambda tid: AgentToolLink(tool_id=tid, delegated_by=actor),
            heal,
        )
    if data.mcp_tool_ids is not None:
        agent.mcp_tool_links = relink(
            agent.mcp_tool_links,
            data.mcp_tool_ids,
            lambda link: link.mcp_tool_id,
            lambda mid: AgentMcpToolLink(mcp_tool_id=mid, delegated_by=actor),
            heal,
        )
    if data.skill_ids is not None:
        agent.skill_links = relink(
            agent.skill_links,
            data.skill_ids,
            lambda link: link.skill_id,
            lambda sid: AgentSkillLink(skill_id=sid, delegated_by=actor),
            heal,
        )
    if data.sub_agent_ids is not None:
        # 자기참조 방어: DB CHECK 제약과 이중 가드
        agent.sub_agent_links = relink(
            agent.sub_agent_links,
            data.sub_agent_ids,
            lambda link: link.sub_agent_id,
            lambda sid: AgentSubAgentLink(sub_agent_id=sid, delegated_by=actor),
            heal,
        )
        for idx, link in enumerate(agent.sub_agent_links):
            link.position = idx
    if data.required_link_ids is not None:  # S29: after the relinks, over every link
        required = set(data.required_link_ids)
        for link, target in linked_targets(agent):
            link.required = target in required
    await db.flush()
    await heal.audit(db, agent.id)
    if before is not None:
        lent = await require_shared_attach(
            db, context, agent.id, before, confirm=data.confirm_delegation
        )
        if lent and data.confirm_delegation:  # D17 audit: what the actor confirmed
            await audit_delegation_confirm(db, context, agent, lent)
        await sync_agent_delegations(db, context, agent.id, added=added)  # F-02 (10.6)
    await db.refresh(
        agent,
        ["model", "tool_links", "mcp_tool_links", "skill_links", "sub_agent_links"],
    )
    return agent


def linked_targets(agent: Agent) -> list[tuple[Any, uuid.UUID]]:
    """Every link of the agent with the id of what it links (S29 ``required``)."""
    return [
        *((link, link.tool_id) for link in agent.tool_links),
        *((link, link.mcp_tool_id) for link in agent.mcp_tool_links),
        *((link, link.skill_id) for link in agent.skill_links),
        *((link, link.sub_agent_id) for link in agent.sub_agent_links),
    ]


def _linked(links: list[Any], attr: str, heal: Reattach) -> set[uuid.UUID]:
    """Ids of kept links; a broken one being re-attached counts as added (10.6, F-03)."""
    return {
        getattr(link, attr)
        for link in links
        if link.link_status != "broken" or getattr(link, attr) not in heal.ids
    }


def _without_kept_broken(data: AgentUpdate, agent: Agent, heal: Reattach) -> AgentUpdate:
    """Kept broken links not re-attached are not re-validated (they must not block)."""
    lists = {
        "tool_ids": (agent.tool_links, "tool_id"),
        "mcp_tool_ids": (agent.mcp_tool_links, "mcp_tool_id"),
        "skill_ids": (agent.skill_links, "skill_id"),
        "sub_agent_ids": (agent.sub_agent_links, "sub_agent_id"),
    }
    update: dict[str, list[uuid.UUID]] = {}
    for name, (links, attr) in lists.items():
        ids = getattr(data, name)
        if ids is None:
            continue
        broken = {getattr(link, attr) for link in links if link.link_status == "broken"}
        update[name] = [i for i in ids if i not in broken - heal.ids]
    return data.model_copy(update=update)


def _added(ids: list[uuid.UUID] | None, linked: set[uuid.UUID]) -> list[uuid.UUID]:
    return [i for i in ids or [] if i not in linked]


async def _count_active_triggers(db: AsyncSession, agent_id: uuid.UUID) -> int:
    from app.models.agent_trigger import AgentTrigger

    result = await db.execute(
        select(func.count())
        .select_from(AgentTrigger)
        .where(AgentTrigger.agent_id == agent_id, AgentTrigger.status == "active")
    )
    return int(result.scalar_one() or 0)
