"""Detached internal Agent work restores the target's persisted private actor."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.error_codes import agent_not_found
from app.models.agent import Agent


async def resolve_agent_actor(db: AsyncSession, agent_id: UUID, user_id: UUID) -> OrgContext:
    agent = (
        await db.execute(
            select(Agent.user_id, Agent.org_id, Agent.tenant_id)
            .where(Agent.id == agent_id, Agent.user_id == user_id)
            .execution_options(autoflush=False)
        )
    ).one_or_none()
    if agent is None:
        raise agent_not_found()
    return await resolve_private_actor(
        db,
        PrivateResourceActor(user_id=agent[0], org_id=agent[1], tenant_id=agent[2]),
    )
