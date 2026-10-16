"""Builder checkpoint identity is required before detached session side effects."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.builder_v3.state import BuilderState
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.authz.scoping import private_scope_predicate
from app.database import SessionTenant
from app.error_codes import session_not_found
from app.models.builder_session import BuilderSession


def state_tenant(state: BuilderState) -> SessionTenant | None:
    """The checkpoint's frozen company (B-13); legacy states stay unbound."""
    try:
        return SessionTenant(UUID(state.get("tenant_id", "")), UUID(state.get("user_id", "")))
    except ValueError:
        return None


async def require_execution_session(db: AsyncSession, state: BuilderState) -> BuilderSession:
    """Restore the frozen checkpoint tuple; missing legacy scope fails closed."""
    try:
        session_id = UUID(state.get("session_id", ""))
        actor = PrivateResourceActor(
            user_id=UUID(state.get("user_id", "")),
            org_id=UUID(state.get("org_id", "")),
            tenant_id=UUID(state.get("tenant_id", "")),
        )
    except ValueError as exc:
        raise session_not_found() from exc
    context = await resolve_private_actor(db, actor)
    row = await db.scalar(
        select(BuilderSession)
        .where(BuilderSession.id == session_id, private_scope_predicate(BuilderSession, context))
        .with_for_update()
        .execution_options(populate_existing=True, autoflush=False)
    )
    if row is None:
        raise session_not_found()
    return row
