"""Per-user agent favorites (``agent_user_preferences``; plan 6.1, 9.1).

Owners keep ``agents.is_favorite`` (source of truth until P8); viewers of shared
agents only ever touch their own preference row.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_preference_scope import favorites_of
from app.models.agent import Agent
from app.models.agent_user_preference import AgentUserPreference


async def set_favorite(
    db: AsyncSession, user_id: uuid.UUID, agent_id: uuid.UUID, value: bool
) -> None:
    row = await db.get(AgentUserPreference, (user_id, agent_id))
    if row is None:
        db.add(AgentUserPreference(user_id=user_id, agent_id=agent_id, is_favorite=value))
    else:
        row.is_favorite = value
    await db.flush()


async def toggle_viewer_favorite(db: AsyncSession, user_id: uuid.UUID, agent: Agent) -> bool:
    """A non-owner's favorite never touches the shared ``agents`` row."""
    current = (await favorites_of(db, user_id, [agent.id])).get(agent.id, False)
    await set_favorite(db, user_id, agent.id, not current)
    return not current
