"""Assistant target reads use the current actor and scoped Agent presentation."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_projection_scope import load_agent_presentation
from app.authz.assistant_scope import READ, assistant_target_scope
from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.models.agent import Agent


async def get_agent_with_eager_load(
    db: AsyncSession,
    agent_id: uuid.UUID,
    context: OrgContext,
    *,
    permission: str = READ,
) -> Agent | None:
    """Keep the Assistant lookup contract while refreshing actor and child scope.

    ``permission`` is re-checked for the actor on every call (plan 11.7); a denial
    is the same ``None`` as a missing agent (an OpenFGA outage raises 503). An
    allowed non-owner reads the agent in its owner's scope. The shared projection
    denies stale actors before target SQL/autoflush and refreshes cached
    relationships from persisted resource parents. Its filtered collections are a
    presentation; writers only append or explicitly delete visible links, leaving
    hidden persisted associations intact.
    """
    scope = await assistant_target_scope(db, context, agent_id, permission)
    if scope is None:
        return None
    return await load_agent_in_scope(db, agent_id, scope, viewer=context)


async def load_agent_in_scope(
    db: AsyncSession, agent_id: uuid.UUID, scope: OrgContext, *, viewer: OrgContext
) -> Agent | None:
    """The scoped presentation once the actor (``viewer``) is authorized."""
    try:
        presentation = await load_agent_presentation(db, agent_id, scope, viewer=viewer)
    except AppError:
        return None
    return presentation.agent if presentation is not None else None
