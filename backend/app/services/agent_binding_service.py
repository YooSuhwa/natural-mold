"""Agent binding validation (split from ``agent_service``)."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate, resource_candidates
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.model import Model
from app.models.tool import AgentToolLink, Tool
from app.schemas.agent import AgentCreate, AgentUpdate


def _build_tool_links(tool_ids: list[uuid.UUID]) -> list[AgentToolLink]:
    """Create AgentToolLink objects for the given tool ids."""
    return [AgentToolLink(tool_id=tid) for tid in tool_ids]


async def _validate_sub_agent_ids_owned(
    db: AsyncSession, sub_agent_ids: list[uuid.UUID], context: OrgContext
) -> None:
    """sub_agent_ids: 실재 + 동일 사용자 소유. 누락 시 400."""
    if not sub_agent_ids:
        return
    result = await db.execute(
        select(Agent.id).where(
            Agent.id.in_(sub_agent_ids),
            private_scope_predicate(Agent, context),
            # Hidden runtime rows can't be attached as sub-agents.
            Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
        )
    )
    valid = {row[0] for row in result.all()}
    invalid = [str(i) for i in sub_agent_ids if i not in valid]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or unauthorized sub_agent_ids: {invalid}",
        )
    for sub_agent_id in sorted(valid, key=lambda item: item.bytes):
        await load_agent_tree_for_execution(db, AgentExecutionTarget(sub_agent_id, context))


async def _validate_mcp_tool_ids_owned(
    db: AsyncSession, mcp_tool_ids: list[uuid.UUID], context: OrgContext
) -> None:
    """mcp_tool_ids: 실재 + (서버 소유주가 user_id). 누락 시 400."""

    if not mcp_tool_ids:
        return
    result = await db.execute(
        select(McpTool.id)
        .join(McpServer, McpTool.server_id == McpServer.id)
        .where(McpTool.id.in_(mcp_tool_ids), private_scope_predicate(McpServer, context))
    )
    valid = {row[0] for row in result.all()}
    invalid = [str(i) for i in mcp_tool_ids if i not in valid]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or unauthorized mcp_tool_ids: {invalid}",
        )


async def _validate_tool_ids_owned(
    db: AsyncSession, tool_ids: list[uuid.UUID], context: OrgContext
) -> None:
    """tool_ids: 실재 + (사용자 소유 OR 시스템 도구). 누락 시 400."""
    if not tool_ids:
        return
    result = await db.execute(
        resource_candidates(select(Tool.id).where(Tool.id.in_(tool_ids)), Tool, context)
    )
    valid = {row[0] for row in result.all()}
    invalid = [str(i) for i in tool_ids if i not in valid]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or unauthorized tool_ids: {invalid}",
        )


async def _validate_model_fallback_ids(db: AsyncSession, fallback_ids: list[uuid.UUID]) -> None:
    """Every fallback id must reference a model row in the catalog.

    The catalog is shared across users (no per-user ownership), so we only
    check existence here. Ordering and deduplication are the caller's
    responsibility — we treat the list as opaque.
    """

    if not fallback_ids:
        return
    result = await db.execute(select(Model.id).where(Model.id.in_(fallback_ids)))
    valid = {row[0] for row in result.all()}
    invalid = [str(i) for i in fallback_ids if i not in valid]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown model_fallback_ids: {invalid}",
        )


async def _validate_skill_ids_owned(
    db: AsyncSession, skill_ids: list[uuid.UUID], context: OrgContext
) -> None:
    """skill_ids: 실재 + 동일 사용자 소유. 누락 시 400."""
    if not skill_ids:
        return
    from app.models.skill import Skill

    result = await db.execute(
        select(Skill.id).where(
            Skill.id.in_(skill_ids),
            private_scope_predicate(Skill, context),
        )
    )
    valid = {row[0] for row in result.all()}
    invalid = [str(i) for i in skill_ids if i not in valid]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or unauthorized skill_ids: {invalid}",
        )


async def _validate_agent_binding_ids(
    db: AsyncSession, data: AgentCreate | AgentUpdate, context: OrgContext
) -> None:
    """Validate the complete requested graph before mutating an Agent or its links."""
    with db.no_autoflush:
        if data.sub_agent_ids:
            await _validate_sub_agent_ids_owned(db, data.sub_agent_ids, context)
        if data.model_fallback_ids:
            await _validate_model_fallback_ids(db, data.model_fallback_ids)
        if data.tool_ids:
            await _validate_tool_ids_owned(db, data.tool_ids, context)
        if data.mcp_tool_ids:
            await _validate_mcp_tool_ids_owned(db, data.mcp_tool_ids, context)
        if data.skill_ids:
            await _validate_skill_ids_owned(db, data.skill_ids, context)
