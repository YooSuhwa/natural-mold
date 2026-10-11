"""Detached private operations derive scope only from their persisted target row."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext, resolve_org_context
from app.exceptions import AppError
from app.models.user import User


@dataclass(frozen=True, slots=True)
class PrivateResourceActor:
    user_id: UUID
    org_id: UUID
    tenant_id: UUID


@dataclass(frozen=True, slots=True)
class PersistedActor:
    id: UUID
    is_super_user: bool


async def require_private_actor(db: AsyncSession, context: OrgContext) -> None:
    """Refresh a captured scope before reads without flushing staged resource writes."""
    with db.no_autoflush:
        await resolve_private_actor(
            db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
        )


async def resolve_private_actor(db: AsyncSession, resource: PrivateResourceActor) -> OrgContext:
    """Reload account/membership; saved preferences never select a detached task's org."""
    with db.no_autoflush:
        user = await db.scalar(
            select(User)
            .where(User.id == resource.user_id)
            .execution_options(populate_existing=True)
        )
        if user is None or not user.is_active:
            raise AppError(code="not_found", message="리소스를 찾을 수 없습니다", status=404)
        actor = PersistedActor(id=user.id, is_super_user=user.is_super_user)
        context = await resolve_org_context(db, actor, cookie=str(resource.org_id))
        if context.tenant_id != resource.tenant_id:
            raise AppError(code="not_found", message="리소스를 찾을 수 없습니다", status=404)
        return context
