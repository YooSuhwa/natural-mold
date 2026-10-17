"""Agent mutation service (split from ``agent_service``)."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.identity import validate_identity_mode
from app.agent_runtime.runtime_policy import runtime_policy_to_json
from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.attach_rules import (
    attach_baseline,
    audit_delegation_confirm,
    require_attachable,
    require_shared_attach,
)
from app.authz.org_context import OrgContext
from app.error_codes import agent_not_found
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.mcp_tool import AgentMcpToolLink
from app.models.skill import AgentSkillLink
from app.schemas.agent import AgentUpdate
from app.services.agent_binding_service import _build_tool_links, _validate_agent_binding_ids
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
    await load_agent_tree_for_execution(db, AgentExecutionTarget(agent.id, context))
    scoped = await get_agent(db, agent.id, context)
    if scoped is None:
        raise agent_not_found()
    agent = scoped
    if data.sub_agent_ids is not None and agent.id in data.sub_agent_ids:
        raise HTTPException(status_code=400, detail="Cannot add self as sub-agent")
    await _validate_agent_binding_ids(db, data, context)
    # D17 (E-06): added account resources need can_share; a shared agent re-confirms.
    await require_attachable(
        db,
        context,
        tool_ids=_added(data.tool_ids, {link.tool_id for link in agent.tool_links}),
        mcp_tool_ids=_added(data.mcp_tool_ids, {link.mcp_tool_id for link in agent.mcp_tool_links}),
    )
    relinks = (data.tool_ids, data.mcp_tool_ids, data.skill_ids, data.sub_agent_ids)
    before = await attach_baseline(db, agent.id) if any(v is not None for v in relinks) else None
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
        # Clear existing links first to avoid PK conflict, then add new ones
        agent.tool_links.clear()
        await db.flush()
        agent.tool_links = _build_tool_links(data.tool_ids)
    if data.mcp_tool_ids is not None:
        agent.mcp_tool_links.clear()
        await db.flush()
        agent.mcp_tool_links = [AgentMcpToolLink(mcp_tool_id=mid) for mid in data.mcp_tool_ids]
    if data.skill_ids is not None:
        agent.skill_links.clear()
        await db.flush()
        agent.skill_links = [AgentSkillLink(skill_id=sid) for sid in data.skill_ids]
    if data.sub_agent_ids is not None:
        # 자기참조 방어: DB CHECK 제약과 이중 가드
        # 소유권/실재 검증 (cross-tenant leak + 500 IntegrityError 방지)
        agent.sub_agent_links.clear()
        await db.flush()
        agent.sub_agent_links = [
            AgentSubAgentLink(sub_agent_id=sid, position=idx)
            for idx, sid in enumerate(data.sub_agent_ids)
        ]
    await db.flush()
    if before is not None:
        lent = await require_shared_attach(
            db, context, agent.id, before, confirm=data.confirm_delegation
        )
        if lent and data.confirm_delegation:  # D17 audit: what the actor confirmed
            await audit_delegation_confirm(db, context, agent, lent)
    await db.refresh(
        agent,
        ["model", "tool_links", "mcp_tool_links", "skill_links", "sub_agent_links"],
    )
    return agent


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
