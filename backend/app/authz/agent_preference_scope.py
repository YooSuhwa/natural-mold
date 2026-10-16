"""The caller's own ``agent_user_preferences`` rows (PRIVATE data, plan 6.1)."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_user_preference import AgentUserPreference


async def favorites_of(
    db: AsyncSession, user_id: UUID, agent_ids: Sequence[UUID]
) -> dict[UUID, bool]:
    """Stored favorite per agent for ``user_id``; agents without a row are absent."""
    if not agent_ids:
        return {}
    rows = await db.execute(
        select(AgentUserPreference.agent_id, AgentUserPreference.is_favorite).where(
            AgentUserPreference.user_id == user_id,
            AgentUserPreference.agent_id.in_(list(agent_ids)),
        )
    )
    return {agent_id: bool(value) for agent_id, value in rows.all()}
