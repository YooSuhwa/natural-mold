"""Agent binding validation (split from ``agent_service``)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_execution_scope import AgentExecutionTarget, load_agent_tree_for_execution
from app.authz.attach_candidates import shared_attachable
from app.authz.audit_actions import DELEGATION_REATTACH
from app.authz.company_catalog import model_scope
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate, resource_candidates
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.model import Model
from app.models.tool import AgentToolLink, Tool
from app.schemas.agent import AgentCreate, AgentUpdate
from app.services import audit_service


def _build_tool_links(
    tool_ids: list[uuid.UUID], delegated_by: uuid.UUID | None = None
) -> list[AgentToolLink]:
    """Create AgentToolLink objects for the given tool ids (``delegated_by``: F-02)."""
    return [AgentToolLink(tool_id=tid, delegated_by=delegated_by) for tid in tool_ids]


def relink[L](
    current: list[L],
    ids: list[uuid.UUID],
    key: Callable[[L], uuid.UUID],
    make: Callable[[uuid.UUID], L],
    heal: Callable[[L, uuid.UUID], None] | None = None,
) -> list[L]:
    """The link list for ``ids``: kept links are reused, so their delegator, ``required``
    and ``link_status`` survive an update (10.6, F-02); only new ids get new rows. A kept
    broken link is offered to ``heal`` (explicit re-attach, F-03)."""
    kept = {key(link): link for link in current}
    links = [kept.pop(i) if i in kept else make(i) for i in ids]
    for link in links:
        if heal is not None and getattr(link, "link_status", None) == "broken":
            heal(link, key(link))
    return links


@dataclass(slots=True)
class Reattach:
    """``relink(heal=...)``: re-delegate the kept broken links named in ``ids`` (10.6)."""

    actor: uuid.UUID
    ids: frozenset[uuid.UUID]
    healed: list[uuid.UUID] = field(default_factory=list)

    def __call__(self, link: Any, rid: uuid.UUID) -> None:
        if rid in self.ids:
            link.link_status, link.broken_reason, link.delegated_by = "active", None, self.actor
            self.healed.append(rid)

    async def audit(self, db: AsyncSession, agent_id: uuid.UUID) -> None:
        if not self.healed:
            return
        await audit_service.record_event(
            db,
            actor_type="user",
            actor_user_id=self.actor,
            action=DELEGATION_REATTACH,
            target_type="agent",
            target_id=agent_id,
            outcome="success",
            metadata={"resource_ids": sorted(str(rid) for rid in self.healed)},
        )


async def _widen(
    db: AsyncSession, ctx: OrgContext, kind: str, ids: list[uuid.UUID], valid: set[uuid.UUID]
) -> set[uuid.UUID]:
    """F-02 (9.1, 10.6): enforced types also accept shared rows the actor may attach."""
    missing = [i for i in ids if i not in valid]
    return valid | await shared_attachable(db, ctx, kind, missing) if missing else valid


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
    valid = await _widen(db, context, "agent", sub_agent_ids, {row[0] for row in result.all()})
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
    valid = await _widen(db, context, "mcp_tool", mcp_tool_ids, {row[0] for row in result.all()})
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
    valid = await _widen(db, context, "tool", tool_ids, {row[0] for row in result.all()})
    invalid = [str(i) for i in tool_ids if i not in valid]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or unauthorized tool_ids: {invalid}",
        )


async def _validate_model_fallback_ids(
    db: AsyncSession, fallback_ids: list[uuid.UUID], tenant_id: uuid.UUID | None = None
) -> None:
    """Every fallback id must reference a platform model or one of the caller company's
    models (E-07); another company's model reads as unknown. Ordering and deduplication
    are the caller's responsibility — we treat the list as opaque.
    """

    if not fallback_ids:
        return
    result = await db.execute(
        select(Model.id).where(Model.id.in_(fallback_ids), model_scope(tenant_id))
    )
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
    valid = await _widen(db, context, "skill", skill_ids, {row[0] for row in result.all()})
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
        if data.model_id is not None:  # FK covers existence; block other companies' models
            foreign = await db.scalar(
                select(Model.id).where(Model.id == data.model_id, ~model_scope(context.tenant_id))
            )
            if foreign is not None:
                raise HTTPException(status_code=400, detail="Unknown model_id")
        if data.model_fallback_ids:
            await _validate_model_fallback_ids(db, data.model_fallback_ids, context.tenant_id)
        if data.tool_ids:
            await _validate_tool_ids_owned(db, data.tool_ids, context)
        if data.mcp_tool_ids:
            await _validate_mcp_tool_ids_owned(db, data.mcp_tool_ids, context)
        if data.skill_ids:
            await _validate_skill_ids_owned(db, data.skill_ids, context)
