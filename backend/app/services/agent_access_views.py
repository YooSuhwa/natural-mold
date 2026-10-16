"""Agent detail/list/summary responses per access mode (plan 9.1, 10.3, S20).

``AUTHZ_MODE=legacy`` returns exactly the pre-P3 bodies with the same queries.
Shadow mode answers with the legacy owner rows and adds ``my_permissions`` (legacy
answer, shadow-sampled). Enforce mode lists grant candidates confirmed by OpenFGA
and serves runners without ``can_view_config`` an ``AgentRunnerView``.
Favorites: owners keep ``agents.is_favorite`` (source of truth until P8); everybody
else reads and writes only their own ``agent_user_preferences`` row.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_access import (
    access_level,
    agents_permissions,
    candidate_agent_ids,
    is_owner,
    owner_context,
    require_agent,
    visible_agent_ids,
)
from app.authz.agent_preference_scope import favorites_of
from app.authz.agent_projection_scope import AgentPresentation, load_agent_presentation
from app.authz.org_context import OrgContext
from app.authz.service import AuthzService
from app.error_codes import agent_not_found
from app.exceptions import AppError
from app.models.agent import Agent
from app.schemas.agent import AgentResponse, AgentSummaryResponse
from app.schemas.agent_access import (
    AgentAccessResponse,
    AgentDetail,
    AgentListItem,
    AgentRunnerView,
    AgentSummaryAccessResponse,
    AgentSummaryItem,
)
from app.services import agent_preferences, agent_query_service, agent_update_service
from app.services.agent_query_service import _selectin_agent

ToResponse = Callable[[Agent], AgentResponse]


def _base(to_response: ToResponse, presentation: AgentPresentation) -> AgentResponse:
    return to_response(presentation.agent).model_copy(
        update={"unread_count": presentation.unread_count}
    )


async def _favorites(
    db: AsyncSession, ctx: OrgContext, agents: Sequence[Agent]
) -> dict[uuid.UUID, bool]:
    viewer = [a.id for a in agents if not is_owner(a, ctx)]
    stored = await favorites_of(db, ctx.user_id, viewer)
    return {a.id: a.is_favorite if is_owner(a, ctx) else stored.get(a.id, False) for a in agents}


async def _permissions(
    authz: AuthzService, ctx: OrgContext, agents: Sequence[Agent]
) -> dict[uuid.UUID, list[str]]:
    return await agents_permissions(authz, ctx, [(a.id, is_owner(a, ctx)) for a in agents])


def _access_item(
    base: AgentResponse, agent: Agent, perms: list[str], favorite: bool
) -> AgentAccessResponse | AgentRunnerView:
    if "can_view_config" in perms:
        fields = base.model_dump() | {"is_favorite": favorite, "my_permissions": perms}
        return AgentAccessResponse.model_validate(fields)
    return AgentRunnerView(
        id=base.id,
        name=base.name,
        description=base.description,
        image_url=base.image_url,
        opener_questions=base.opener_questions,
        owner_id=agent.user_id,
        is_favorite=favorite,
        unread_count=base.unread_count,
        my_level=access_level(perms),
        my_permissions=perms,
    )


async def _shared_presentation(
    db: AsyncSession, ctx: OrgContext, agent: Agent
) -> AgentPresentation:
    presentation = await load_agent_presentation(db, agent.id, owner_context(agent), viewer=ctx)
    if presentation is None:
        raise agent_not_found()
    return presentation


async def authorized_agent(
    db: AsyncSession,
    authz: AuthzService,
    ctx: OrgContext,
    agent_id: uuid.UUID,
    permission: str,
    *,
    not_found: AppError | None = None,
) -> Agent:
    """``RES(agent, permission)`` for mutations; legacy answer = the owner-scoped row."""
    legacy = await agent_query_service.get_agent(db, agent_id, ctx)
    return await require_agent(
        db,
        authz,
        ctx,
        agent_id,
        permission,
        legacy=legacy,
        not_found=not_found or agent_not_found(),
        options=_selectin_agent(),
    )


async def toggle_favorite(db: AsyncSession, ctx: OrgContext, agent: Agent) -> tuple[Agent, bool]:
    """Owner: the legacy column; shared viewer: only their own preference row."""
    if is_owner(agent, ctx):
        updated = await agent_update_service.toggle_favorite(db, agent, ctx)
        return updated, updated.is_favorite
    return agent, await agent_preferences.toggle_viewer_favorite(db, ctx.user_id, agent)


async def present_agent(
    db: AsyncSession,
    authz: AuthzService,
    ctx: OrgContext,
    agent_id: uuid.UUID,
    to_response: ToResponse,
) -> AgentDetail:
    """``GET /api/agents/{id}`` (``RES(agent, can_run)``) and mutation responses."""
    presentation = await agent_query_service.get_agent_projection(db, agent_id, ctx)
    legacy = presentation.agent if presentation is not None else None
    row = await require_agent(
        db, authz, ctx, agent_id, "can_run", legacy=legacy, not_found=agent_not_found()
    )
    if presentation is None:  # shared agent allowed by OpenFGA (enforce mode)
        presentation = await _shared_presentation(db, ctx, row)
    base = _base(to_response, presentation)
    if authz.mode == "legacy":
        return base
    favorite = (await _favorites(db, ctx, [row]))[row.id]
    perms = (await _permissions(authz, ctx, [row]))[row.id]
    return _access_item(base, row, perms, favorite)


async def list_agent_views(
    db: AsyncSession,
    authz: AuthzService,
    ctx: OrgContext,
    legacy: Sequence[Agent],
    to_response: ToResponse,
) -> list[AgentListItem]:
    """``GET /api/agents`` outside legacy mode: owned rows first, then shared ones."""
    owned = {agent.id: agent for agent in legacy}
    shared_ids = [i for i in await candidate_agent_ids(db, ctx) if i not in owned]
    kept = await visible_agent_ids(authz, ctx, [*owned, *shared_ids], legacy=list(owned))
    shared_kept = [i for i in kept if i not in owned]
    shared = {a.id: a for a in await db.scalars(select(Agent).where(Agent.id.in_(shared_kept)))}
    rows = [owned.get(i) or shared[i] for i in kept]
    favorites = await _favorites(db, ctx, rows)
    perms = await _permissions(authz, ctx, rows)
    items: list[AgentListItem] = []
    for row in rows:
        if row.id in owned:
            base = to_response(row)
        else:  # owner-scoped links differ per owner, so shared rows load one by one
            base = _base(to_response, await _shared_presentation(db, ctx, row))
        items.append(_access_item(base, row, perms[row.id], favorites[row.id]))
    return items


async def list_summary_views(
    db: AsyncSession,
    authz: AuthzService,
    ctx: OrgContext,
    legacy: Sequence[dict],
) -> list[AgentSummaryItem]:
    """``GET /api/agents/summary`` outside legacy mode, with ``my_permissions``."""
    owned = [row["id"] for row in legacy]
    shared_ids = [i for i in await candidate_agent_ids(db, ctx) if i not in set(owned)]
    kept = await visible_agent_ids(authz, ctx, [*owned, *shared_ids], legacy=owned)
    rows: Sequence[dict] = legacy
    if authz.enforces("agent"):
        rows = await agent_query_service.list_agent_summaries(
            db, ctx, agent_scope=Agent.id.in_(kept)
        )
    allowed = set(kept)
    rows = [row for row in rows if row["id"] in allowed]
    ids = [row["id"] for row in rows]
    agents = {a.id: a for a in await db.scalars(select(Agent).where(Agent.id.in_(ids)))}
    ordered = [agents[i] for i in ids]
    favorites = await _favorites(db, ctx, ordered)
    perms = await _permissions(authz, ctx, ordered)
    items: list[AgentSummaryItem] = []
    for row in rows:
        fields = AgentSummaryResponse.model_validate(row).model_dump()
        fields |= {"is_favorite": favorites[row["id"]], "my_permissions": perms[row["id"]]}
        items.append(AgentSummaryAccessResponse.model_validate(fields))
    return items
