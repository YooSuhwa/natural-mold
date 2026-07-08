from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent_runtime.identity import make_agent_runtime_name, validate_identity_mode
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.model import Model
from app.models.skill import AgentSkillLink
from app.models.template import Template
from app.models.tool import AgentToolLink, Tool
from app.schemas.agent import AgentCreate, AgentUpdate
from app.services.agent_image_paths import build_agent_image_url

logger = logging.getLogger(__name__)


def _selectin_agent() -> list:
    """Standard eager-loading options for Agent queries.

    AgentSubAgentLink.sub_agent는 model 측에 lazy="joined"가 걸려 있어 link 로드 시
    같이 들어온다 — 여기서 추가 selectinload는 불필요(중복).
    """
    return [
        selectinload(Agent.model),
        selectinload(Agent.tool_links).selectinload(AgentToolLink.tool),
        selectinload(Agent.mcp_tool_links).selectinload(AgentMcpToolLink.mcp_tool),
        selectinload(Agent.skill_links).selectinload(AgentSkillLink.skill),
        selectinload(Agent.sub_agent_links),
    ]


async def list_agents(db: AsyncSession, user_id: uuid.UUID) -> list[Agent]:
    """List user's agents with ``last_used_at`` set on each row.

    ``last_used_at`` is derived from ``max(conversations.updated_at)`` per
    agent so the sidebar can surface "most-recently chatted with" without
    needing a dedicated column. The value is attached as a transient
    attribute (not persisted) so the response serializer picks it up.
    """

    from sqlalchemy import func

    from app.models.conversation import Conversation

    last_used_subq = (
        select(
            Conversation.agent_id.label("agent_id"),
            func.max(Conversation.updated_at).label("last_used_at"),
            func.coalesce(func.sum(Conversation.unread_count), 0).label("unread_count"),
        )
        .group_by(Conversation.agent_id)
        .subquery()
    )

    result = await db.execute(
        select(Agent, last_used_subq.c.last_used_at, last_used_subq.c.unread_count)
        .outerjoin(last_used_subq, Agent.id == last_used_subq.c.agent_id)
        .where(
            Agent.user_id == user_id,
            # Hidden runtime rows (skill builder 등) never surface in lists.
            Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
        )
        .options(*_selectin_agent())
        .order_by(func.coalesce(last_used_subq.c.last_used_at, Agent.created_at).desc())
    )
    rows = result.all()
    agents: list[Agent] = []
    for agent, last_used, unread_count in rows:
        # Stash on the ORM instance — picked up by ``_agent_to_response``.
        agent._last_used_at = last_used
        agent._unread_count = int(unread_count or 0)
        agents.append(agent)
    return agents


async def list_agent_summaries(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    """List user's agents as lean dashboard/sidebar summary rows."""

    from app.models.conversation import Conversation

    user_agent_ids = (
        select(Agent.id)
        .where(
            Agent.user_id == user_id,
            Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
        )
        .subquery()
    )

    conversation_summary = (
        select(
            Conversation.agent_id.label("agent_id"),
            func.max(Conversation.updated_at).label("last_used_at"),
            func.coalesce(func.sum(Conversation.unread_count), 0).label("unread_count"),
        )
        .where(Conversation.agent_id.in_(select(user_agent_ids.c.id)))
        .group_by(Conversation.agent_id)
        .subquery()
    )
    tool_summary = (
        select(
            AgentToolLink.agent_id.label("agent_id"),
            func.count(AgentToolLink.tool_id).label("tool_count"),
        )
        .group_by(AgentToolLink.agent_id)
        .subquery()
    )
    mcp_tool_summary = (
        select(
            AgentMcpToolLink.agent_id.label("agent_id"),
            func.count(AgentMcpToolLink.mcp_tool_id).label("mcp_tool_count"),
        )
        .group_by(AgentMcpToolLink.agent_id)
        .subquery()
    )

    result = await db.execute(
        select(
            Agent.id,
            Agent.name,
            Agent.description,
            Agent.status,
            Agent.is_favorite,
            Agent.image_path,
            Agent.model_fallback_list,
            Agent.created_at,
            Agent.updated_at,
            Model.display_name.label("model_display_name"),
            conversation_summary.c.last_used_at,
            conversation_summary.c.unread_count,
            tool_summary.c.tool_count,
            mcp_tool_summary.c.mcp_tool_count,
        )
        .join(Model, Agent.model_id == Model.id, isouter=True)
        .outerjoin(conversation_summary, Agent.id == conversation_summary.c.agent_id)
        .outerjoin(tool_summary, Agent.id == tool_summary.c.agent_id)
        .outerjoin(mcp_tool_summary, Agent.id == mcp_tool_summary.c.agent_id)
        .where(
            Agent.user_id == user_id,
            Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
        )
        .order_by(func.coalesce(conversation_summary.c.last_used_at, Agent.created_at).desc())
    )

    summaries: list[dict] = []
    for row in result.mappings():
        tool_count = int(row["tool_count"] or 0) + int(row["mcp_tool_count"] or 0)
        image_url = build_agent_image_url(
            row["id"],
            updated_at=row["updated_at"],
            image_path=row["image_path"],
        )
        summaries.append(
            {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "status": row["status"],
                "is_favorite": row["is_favorite"],
                "image_url": image_url,
                "model_display_name": row["model_display_name"],
                "tool_count": tool_count,
                "fallback_count": len(row["model_fallback_list"] or []),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "last_used_at": row["last_used_at"],
                "unread_count": int(row["unread_count"] or 0),
            }
        )
    return summaries


async def get_agent(db: AsyncSession, agent_id: uuid.UUID, user_id: uuid.UUID) -> Agent | None:
    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent_id, Agent.user_id == user_id)
        .options(*_selectin_agent())
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        return None
    from app.models.conversation import Conversation

    unread_result = await db.execute(
        select(func.coalesce(func.sum(Conversation.unread_count), 0)).where(
            Conversation.agent_id == agent_id
        )
    )
    agent_with_unread = cast(Any, agent)
    agent_with_unread._unread_count = int(unread_result.scalar_one() or 0)
    return agent


def _build_tool_links(tool_ids: list[uuid.UUID]) -> list[AgentToolLink]:
    """Create AgentToolLink objects for the given tool ids."""
    return [AgentToolLink(tool_id=tid) for tid in tool_ids]


async def _validate_sub_agent_ids_owned(
    db: AsyncSession, sub_agent_ids: list[uuid.UUID], user_id: uuid.UUID
) -> None:
    """sub_agent_ids: 실재 + 동일 사용자 소유. 누락 시 400."""
    if not sub_agent_ids:
        return
    result = await db.execute(
        select(Agent.id).where(
            Agent.id.in_(sub_agent_ids),
            Agent.user_id == user_id,
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


async def _validate_mcp_tool_ids_owned(
    db: AsyncSession, mcp_tool_ids: list[uuid.UUID], user_id: uuid.UUID
) -> None:
    """mcp_tool_ids: 실재 + (서버 소유주가 user_id). 누락 시 400."""

    if not mcp_tool_ids:
        return
    result = await db.execute(
        select(McpTool.id)
        .join(McpServer, McpTool.server_id == McpServer.id)
        .where(McpTool.id.in_(mcp_tool_ids), McpServer.user_id == user_id)
    )
    valid = {row[0] for row in result.all()}
    invalid = [str(i) for i in mcp_tool_ids if i not in valid]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or unauthorized mcp_tool_ids: {invalid}",
        )


async def _validate_tool_ids_owned(
    db: AsyncSession, tool_ids: list[uuid.UUID], user_id: uuid.UUID
) -> None:
    """tool_ids: 실재 + (사용자 소유 OR 시스템 도구). 누락 시 400."""
    if not tool_ids:
        return
    result = await db.execute(
        select(Tool.id).where(
            Tool.id.in_(tool_ids),
            Tool.visible_to(user_id),
        )
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
    db: AsyncSession, skill_ids: list[uuid.UUID], user_id: uuid.UUID
) -> None:
    """skill_ids: 실재 + 동일 사용자 소유. 누락 시 400."""
    if not skill_ids:
        return
    from app.models.skill import Skill

    result = await db.execute(
        select(Skill.id).where(
            Skill.id.in_(skill_ids),
            Skill.user_id == user_id,
        )
    )
    valid = {row[0] for row in result.all()}
    invalid = [str(i) for i in skill_ids if i not in valid]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or unauthorized skill_ids: {invalid}",
        )


async def _install_template_skills(
    db: AsyncSession, slugs: list[str], user_id: uuid.UUID
) -> list[uuid.UUID]:
    """Install system marketplace skills referenced by a template (by slug).

    Re-uses an existing installation when the user already has one
    (``install_mode="reuse_or_update"``). Seed drift — a slug without a
    published system item, or an installation whose skill row was deleted —
    is skipped with a warning instead of failing agent creation: the agent
    is still usable and the skill can be attached manually.
    """

    from app.dependencies import CurrentUser
    from app.marketplace.install_service import install_item
    from app.marketplace.schemas import InstallMarketplaceItemIn
    from app.models.marketplace import MarketplaceItem
    from app.models.skill import Skill
    from app.models.user import User

    user_row = await db.get(User, user_id)
    if user_row is None:
        return []
    current_user = CurrentUser(
        id=user_row.id,
        email=user_row.email,
        name=user_row.name,
        is_super_user=bool(user_row.is_super_user),
    )

    skill_ids: list[uuid.UUID] = []
    for slug in slugs:
        item_id = (
            await db.execute(
                select(MarketplaceItem.id)
                .where(MarketplaceItem.resource_type == "skill")
                .where(MarketplaceItem.is_system.is_(True))
                .where(MarketplaceItem.status == "published")
                .where(
                    or_(
                        MarketplaceItem.slug == slug,
                        MarketplaceItem.source_external_id == slug,
                    )
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if item_id is None:
            logger.warning(
                "template skill slug %r has no published system marketplace item; skipping",
                slug,
            )
            continue
        try:
            installation = await install_item(
                db,
                item_id=item_id,
                user=current_user,
                body=InstallMarketplaceItemIn(),
            )
        except HTTPException:
            logger.warning("template skill %r auto-install failed; skipping", slug, exc_info=True)
            continue
        if installation.installed_skill_id is not None:
            skill_ids.append(installation.installed_skill_id)

    if not skill_ids:
        return []
    # A re-used installation can point at a skill the user has since deleted;
    # attach only skill rows that still exist and belong to the user.
    result = await db.execute(
        select(Skill.id).where(Skill.id.in_(skill_ids), Skill.user_id == user_id)
    )
    valid = {row[0] for row in result.all()}
    dangling = [str(s) for s in skill_ids if s not in valid]
    if dangling:
        logger.warning("template skill installations point at missing skills: %s", dangling)
    return [s for s in skill_ids if s in valid]


async def toggle_favorite(db: AsyncSession, agent: Agent) -> Agent:
    agent.is_favorite = not agent.is_favorite
    await db.flush()
    await db.refresh(
        agent,
        ["model", "tool_links", "mcp_tool_links", "skill_links", "sub_agent_links"],
    )
    return agent


async def create_agent(db: AsyncSession, data: AgentCreate, user_id: uuid.UUID) -> Agent:
    fallback_ids = data.model_fallback_ids or []
    if fallback_ids:
        await _validate_model_fallback_ids(db, fallback_ids)

    agent_id = uuid.uuid4()
    agent = Agent(
        id=agent_id,
        user_id=user_id,
        runtime_name=make_agent_runtime_name(agent_id),
        identity_mode=validate_identity_mode(data.identity_mode),
        name=data.name,
        description=data.description,
        system_prompt=data.system_prompt,
        model_id=data.model_id,
        model_params=data.model_params,
        middleware_configs=[mc.model_dump() for mc in data.middleware_configs]
        if data.middleware_configs
        else None,
        opener_questions=data.opener_questions,
        model_fallback_list=[str(fid) for fid in fallback_ids] if fallback_ids else None,
        template_id=data.template_id,
    )

    template: Template | None = None
    if data.template_id:
        template = await db.get(Template, data.template_id)

    # Collect tools to link
    tool_ids_to_link: list[uuid.UUID] = []

    if data.tool_ids:
        tool_ids_to_link.extend(data.tool_ids)
    elif template and template.recommended_tools:
        # Auto-link from template recommended tools (by name)
        lower_names = [n.lower() for n in template.recommended_tools]
        result = await db.execute(
            select(Tool.id).where(
                Tool.visible_to(user_id),
                func.lower(Tool.name).in_(lower_names),
            )
        )
        tool_ids_to_link.extend(r[0] for r in result.all())

    if tool_ids_to_link:
        await _validate_tool_ids_owned(db, tool_ids_to_link, user_id)
        agent.tool_links = _build_tool_links(tool_ids_to_link)

    if data.mcp_tool_ids:
        await _validate_mcp_tool_ids_owned(db, data.mcp_tool_ids, user_id)
        agent.mcp_tool_links = [AgentMcpToolLink(mcp_tool_id=mid) for mid in data.mcp_tool_ids]

    if data.skill_ids:
        await _validate_skill_ids_owned(db, data.skill_ids, user_id)
        agent.skill_links = [AgentSkillLink(skill_id=sid) for sid in data.skill_ids]
    elif template and template.recommended_skill_slugs:
        # Auto-install system marketplace skills referenced by the template,
        # then attach them (mirrors the recommended_tools auto-link above).
        auto_skill_ids = await _install_template_skills(
            db,
            [str(s) for s in template.recommended_skill_slugs],
            user_id,
        )
        if auto_skill_ids:
            agent.skill_links = [AgentSkillLink(skill_id=sid) for sid in auto_skill_ids]

    if data.sub_agent_ids:
        await _validate_sub_agent_ids_owned(db, data.sub_agent_ids, user_id)
        agent.sub_agent_links = [
            AgentSubAgentLink(sub_agent_id=sid, position=idx)
            for idx, sid in enumerate(data.sub_agent_ids)
        ]

    db.add(agent)
    await db.flush()
    await db.refresh(
        agent,
        ["model", "tool_links", "mcp_tool_links", "skill_links", "sub_agent_links"],
    )
    return agent


async def update_agent(db: AsyncSession, agent: Agent, data: AgentUpdate) -> Agent:
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
    if data.opener_questions is not None:
        agent.opener_questions = data.opener_questions
    if data.model_fallback_ids is not None:
        await _validate_model_fallback_ids(db, data.model_fallback_ids)
        agent.model_fallback_list = (
            [str(fid) for fid in data.model_fallback_ids] if data.model_fallback_ids else None
        )
    if data.tool_ids is not None:
        await _validate_tool_ids_owned(db, data.tool_ids, agent.user_id)
        # Clear existing links first to avoid PK conflict, then add new ones
        agent.tool_links.clear()
        await db.flush()
        agent.tool_links = _build_tool_links(data.tool_ids)
    if data.mcp_tool_ids is not None:
        await _validate_mcp_tool_ids_owned(db, data.mcp_tool_ids, agent.user_id)
        agent.mcp_tool_links.clear()
        await db.flush()
        agent.mcp_tool_links = [AgentMcpToolLink(mcp_tool_id=mid) for mid in data.mcp_tool_ids]
    if data.skill_ids is not None:
        await _validate_skill_ids_owned(db, data.skill_ids, agent.user_id)
        agent.skill_links.clear()
        await db.flush()
        agent.skill_links = [AgentSkillLink(skill_id=sid) for sid in data.skill_ids]
    if data.sub_agent_ids is not None:
        # 자기참조 방어: DB CHECK 제약과 이중 가드
        if agent.id in data.sub_agent_ids:
            raise HTTPException(status_code=400, detail="Cannot add self as sub-agent")
        # 소유권/실재 검증 (cross-tenant leak + 500 IntegrityError 방지)
        await _validate_sub_agent_ids_owned(db, data.sub_agent_ids, agent.user_id)
        agent.sub_agent_links.clear()
        await db.flush()
        agent.sub_agent_links = [
            AgentSubAgentLink(sub_agent_id=sid, position=idx)
            for idx, sid in enumerate(data.sub_agent_ids)
        ]
    await db.flush()
    await db.refresh(
        agent,
        ["model", "tool_links", "mcp_tool_links", "skill_links", "sub_agent_links"],
    )
    return agent


async def _count_active_triggers(db: AsyncSession, agent_id: uuid.UUID) -> int:
    from app.models.agent_trigger import AgentTrigger

    result = await db.execute(
        select(func.count())
        .select_from(AgentTrigger)
        .where(AgentTrigger.agent_id == agent_id, AgentTrigger.status == "active")
    )
    return int(result.scalar_one() or 0)


async def delete_agent(db: AsyncSession, agent: Agent) -> None:
    await db.delete(agent)
    await db.flush()
