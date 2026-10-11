"""Assistant target reads use the current actor and scoped Agent presentation."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_projection_scope import load_agent_presentation
from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.models.agent import Agent


async def get_agent_with_eager_load(
    db: AsyncSession,
    agent_id: uuid.UUID,
    context: OrgContext,
) -> Agent | None:
    """Keep the Assistant lookup contract while refreshing actor and child scope.

    The shared projection denies stale actors before target SQL/autoflush and
    refreshes cached relationships from persisted resource parents. Its filtered
    collections are a presentation; writers only append or explicitly delete
    visible links, leaving hidden persisted associations intact.
    """
    try:
        presentation = await load_agent_presentation(db, agent_id, context)
    except AppError:
        return None
    return presentation.agent if presentation is not None else None
