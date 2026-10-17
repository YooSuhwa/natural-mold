"""Company membership predicates used by the SSO policy (plan 10.10; H-08)."""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Tenant, TenantMember, TenantRoleGrant
from app.models.refresh_token import RefreshToken
from app.models.user import User


async def active_member_tenants(db: AsyncSession, user_id: UUID) -> list[Tenant]:
    """Companies where ``user_id`` is an active member."""
    rows = await db.scalars(
        select(Tenant)
        .join(TenantMember, TenantMember.tenant_id == Tenant.id)
        .where(TenantMember.user_id == user_id, TenantMember.status == "active")
        .order_by(Tenant.id)
    )
    return list(rows)


async def active_admin_ids(
    db: AsyncSession, tenant_id: UUID, user_ids: Sequence[UUID]
) -> set[UUID]:
    """The subset of ``user_ids`` that are active, active-member company admins."""
    rows = await db.scalars(
        select(TenantRoleGrant.user_id)
        .join(
            TenantMember,
            (TenantMember.tenant_id == TenantRoleGrant.tenant_id)
            & (TenantMember.user_id == TenantRoleGrant.user_id),
        )
        .join(User, User.id == TenantRoleGrant.user_id)
        .where(
            TenantRoleGrant.tenant_id == tenant_id,
            TenantRoleGrant.role == "admin",
            TenantRoleGrant.user_id.in_(user_ids),
            TenantMember.status == "active",
            User.is_active.is_(True),
        )
    )
    return set(rows)


async def password_member_count(db: AsyncSession, tenant_id: UUID, excluded: Sequence[UUID]) -> int:
    """Active members who have a password and are not in ``excluded``."""
    count = await db.scalar(
        select(func.count(User.id))
        .join(TenantMember, TenantMember.user_id == User.id)
        .where(
            TenantMember.tenant_id == tenant_id,
            TenantMember.status == "active",
            User.hashed_password.is_not(None),
            User.id.not_in(list(excluded) or [UUID(int=0)]),
        )
    )
    return count or 0


async def revoke_password_sessions(db: AsyncSession, tenant_id: UUID, keep: Sequence[UUID]) -> None:
    """Revoke password-origin refresh tokens of the company's active members (H-08).

    Called when a company switches to SSO required; ``keep`` are the break-glass users.
    NULL ``auth_method`` (sessions older than H-08) counts as password-origin.
    """
    members = select(TenantMember.user_id).where(
        TenantMember.tenant_id == tenant_id, TenantMember.status == "active"
    )
    await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id.in_(members),
            RefreshToken.user_id.not_in(list(keep) or [UUID(int=0)]),
            RefreshToken.revoked_at.is_(None),
            or_(RefreshToken.auth_method.is_(None), RefreshToken.auth_method == "password"),
        )
        .values(revoked_at=datetime.now(UTC))
    )
