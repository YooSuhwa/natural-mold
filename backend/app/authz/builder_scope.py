"""Detached Builder work derives scope from its persisted authorized session."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.error_codes import session_not_found
from app.models.builder_session import BuilderSession


async def resolve_builder_actor(db: AsyncSession, session_id: UUID, user_id: UUID) -> OrgContext:
    session = (
        await db.execute(
            select(BuilderSession.user_id, BuilderSession.org_id, BuilderSession.tenant_id)
            .where(BuilderSession.id == session_id, BuilderSession.user_id == user_id)
            .execution_options(autoflush=False)
        )
    ).one_or_none()
    if session is None:
        raise session_not_found()
    return await resolve_private_actor(
        db,
        PrivateResourceActor(user_id=session[0], org_id=session[1], tenant_id=session[2]),
    )
