"""Login-state cleanup when company membership ends (plan 8.1, B-15).

Removal from a user's last company and permanent company deletion force-logout
through the existing refresh whitelist revocation. Company or member suspension
must never call these: suspended sessions keep their refresh tokens and are
denied per request. Leftover access tokens (at most their lifetime) are denied
by the fresh membership and company-status checks of every org request.
"""

from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.organization import TenantMember
from app.services import auth_service


async def revoke_sessions_without_company(db: AsyncSession, user_id: UUID) -> bool:
    """Revoke every refresh token when no non-removed company membership remains.

    Call inside the removal transaction after the membership row is marked removed;
    suspended memberships still count as membership. The caller must already hold
    the ``users`` row FOR UPDATE (as ``exit_tenant_member`` does): refresh rotation
    share-locks that row first, so the lock closes the rotate-vs-revoke race.
    """
    remaining = await db.scalar(
        select(TenantMember.tenant_id)
        .where(TenantMember.user_id == user_id, TenantMember.status != "removed")
        .limit(1)
    )
    if remaining is not None:
        return False
    await auth_service.revoke_all_refresh_tokens(db, user_id)
    return True


async def revoke_sessions_for_deleted_tenant(db: AsyncSession, tenant_id: UUID) -> tuple[UUID, ...]:
    """Force-logout current members for whom the deleted company is the last one.

    Members with another non-removed company keep their sessions. The purge flow
    (E08/I07) calls this under its company lock, after locking the affected
    ``users`` rows FOR UPDATE and before deleting the membership rows, and commits
    with the deletion.
    """
    other = aliased(TenantMember)
    elsewhere = exists().where(
        other.user_id == TenantMember.user_id,
        other.tenant_id != tenant_id,
        other.status != "removed",
    )
    user_ids = tuple(
        (
            await db.scalars(
                select(TenantMember.user_id)
                .where(
                    TenantMember.tenant_id == tenant_id,
                    TenantMember.status != "removed",
                    ~elsewhere,
                )
                .order_by(TenantMember.user_id)
            )
        ).all()
    )
    for user_id in user_ids:
        await auth_service.revoke_all_refresh_tokens(db, user_id)
    return user_ids
