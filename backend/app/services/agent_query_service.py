"""Agent read/query service (split from ``agent_service``)."""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.authz.agent_projection_scope import (
    AgentPresentation,
    load_agent_presentation,
    mcp_link_scope,
    projection_options,
    tool_link_scope,
)
from app.authz.org_context import OrgContext
from app.authz.private_actor import (
    PrivateResourceActor,
    require_private_actor,
    resolve_private_actor,
)
from app.authz.scoping import private_scope_predicate
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.mcp_tool import AgentMcpToolLink
from app.models.model import Model
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink
from app.services.agent_image_paths import build_agent_image_url


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


async def list_agents(db: AsyncSession, context: OrgContext) -> list[Agent]:
    """List user's agents with ``last_used_at`` set on each row.

    ``last_used_at`` is derived from ``max(conversations.updated_at)`` per
    agent so the sidebar can surface "most-recently chatted with" without
    needing a dedicated column. The value is attached as a transient
    attribute (not persisted) so the response serializer picks it up.
    """

    await require_private_actor(db, context)
    from sqlalchemy import func

    from app.models.conversation import Conversation

    last_used_subq = (
        select(
            Conversation.agent_id.label("agent_id"),
            func.max(Conversation.updated_at).label("last_used_at"),
            func.coalesce(func.sum(Conversation.unread_count), 0).label("unread_count"),
        )
        .where(private_scope_predicate(Conversation, context))
        .group_by(Conversation.agent_id)
        .subquery()
    )

    result = await db.execute(
        select(Agent, last_used_subq.c.last_used_at, last_used_subq.c.unread_count)
        .outerjoin(last_used_subq, Agent.id == last_used_subq.c.agent_id)
        .where(
            private_scope_predicate(Agent, context),
            # Hidden runtime rows (skill builder 등) never surface in lists.
            Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
            Agent.archived_at.is_(None),
        )
        .options(*projection_options(context))
        .execution_options(autoflush=False, populate_existing=True)
        .order_by(func.coalesce(last_used_subq.c.last_used_at, Agent.created_at).desc())
    )
    rows = result.all()
    agents: list[Agent] = []
    for agent, last_used, unread_count in rows:
        # Stash on the ORM instance — picked up by ``_agent_to_response``.
        agent._last_used_at = last_used  # noqa: SLF001 — deliberate stash, read by _agent_to_response
        agent._unread_count = int(unread_count or 0)  # noqa: SLF001
        agents.append(agent)
    return agents


async def list_agent_summaries(
    db: AsyncSession, context: OrgContext, *, agent_scope: ColumnElement[bool] | None = None
) -> list[dict]:
    """List user's agents as lean dashboard/sidebar summary rows.

    ``agent_scope`` replaces the owner scope with an already authorized id set
    (enforce mode); conversation and link counts stay in the caller's scope.
    """

    await require_private_actor(db, context)
    scope = agent_scope if agent_scope is not None else private_scope_predicate(Agent, context)
    from app.models.conversation import Conversation

    user_agent_ids = (
        select(Agent.id)
        .where(
            scope,
            Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
            Agent.archived_at.is_(None),
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
        .where(private_scope_predicate(Conversation, context))
        .group_by(Conversation.agent_id)
        .subquery()
    )
    tool_summary = (
        select(
            AgentToolLink.agent_id.label("agent_id"),
            func.count(AgentToolLink.tool_id).label("tool_count"),
        )
        .where(tool_link_scope(context))
        .group_by(AgentToolLink.agent_id)
        .subquery()
    )
    mcp_tool_summary = (
        select(
            AgentMcpToolLink.agent_id.label("agent_id"),
            func.count(AgentMcpToolLink.mcp_tool_id).label("mcp_tool_count"),
        )
        .where(mcp_link_scope(context))
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
            scope,
            Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
            Agent.archived_at.is_(None),
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


async def get_agent(db: AsyncSession, agent_id: uuid.UUID, context: OrgContext) -> Agent | None:
    with db.no_autoflush:
        await resolve_private_actor(
            db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
        )
        result = await db.execute(
            select(Agent)
            .where(Agent.id == agent_id, private_scope_predicate(Agent, context))
            .options(*_selectin_agent())
            .execution_options(autoflush=False, populate_existing=True)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return None
        from app.models.conversation import Conversation

        unread_result = await db.execute(
            select(func.coalesce(func.sum(Conversation.unread_count), 0)).where(
                Conversation.agent_id == agent_id, private_scope_predicate(Conversation, context)
            )
        )
        agent_with_unread = cast(Any, agent)
        agent_with_unread._unread_count = int(unread_result.scalar_one() or 0)  # noqa: SLF001 — deliberate stash
        return agent


async def get_agent_projection(
    db: AsyncSession, agent_id: uuid.UUID, context: OrgContext
) -> AgentPresentation | None:
    """Read current presentation links without changing persisted associations."""
    return await load_agent_presentation(db, agent_id, context)
