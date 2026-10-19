"""Capture helpers for membership events with unusual scopes (SSO logins, account erasure)."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.membership_projection import Subject, UserSubject
from app.authz.membership_sync import MembershipSync
from app.authz.tenant_org_write_locks import TenantOrgWriteTarget, lock_tenant_org_writes
from app.models.organization import TenantMember


@dataclass(slots=True)
class LazySync:
    """Capture only right before the first change (logins that change nothing read nothing)."""

    tenant_id: UUID
    subjects: tuple[Subject, ...]
    sync: MembershipSync | None = None

    async def before_change(self, db: AsyncSession) -> None:
        if self.sync is None:
            self.sync = await MembershipSync.capture(db, self.tenant_id, self.subjects)

    async def queue(self, db: AsyncSession) -> tuple[UUID, ...]:
        return () if self.sync is None else await self.sync.queue(db)


async def capture_account(db: AsyncSession, user_id: UUID) -> list[MembershipSync]:
    """One capture per company the account belongs to (account erasure, 10.7)."""
    tenants = list(
        await db.scalars(
            select(TenantMember.tenant_id).where(TenantMember.user_id == user_id).distinct()
        )
    )
    # Company tuples mark every organization: lock them all (tenant, then UUID-byte order).
    await lock_tenant_org_writes(db, [TenantOrgWriteTarget(t, None) for t in tenants])
    return [
        await MembershipSync.capture(db, tenant, [UserSubject(tenant, user_id)])
        for tenant in sorted(tenants)
    ]
