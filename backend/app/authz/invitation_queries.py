"""Token lookup reveals no invitation metadata outside the acceptance service."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.authz.tenant_org_write_locks import lock_tenant_org_write
from app.models.organization import Organization, Tenant
from app.models.organization_group import OrgInvitation


async def invitation_scope_hint(db: AsyncSession, digest: str) -> tuple[UUID, UUID]:
    hint = (
        (
            await db.execute(
                select(OrgInvitation.org_id, OrgInvitation.tenant_id).where(
                    OrgInvitation.token_hash == digest
                )
            )
        )
        .tuples()
        .first()
    )
    if hint is None:
        raise authorization_error("invitation_invalid")
    return hint


async def locked_invitation_from_hint(
    db: AsyncSession, digest: str, org_id: UUID, tenant_id: UUID
) -> OrgInvitation:
    """Revalidate the token child after callers hold its Tenant and Organization."""
    row = await db.scalar(
        select(OrgInvitation)
        .where(
            OrgInvitation.token_hash == digest,
            OrgInvitation.org_id == org_id,
            OrgInvitation.tenant_id == tenant_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise authorization_error("invitation_invalid")
    return row


async def locked_invitation_scope(
    db: AsyncSession, digest: str
) -> tuple[Organization, Tenant, OrgInvitation]:
    org_id, tenant_id = await invitation_scope_hint(db, digest)
    locked = await lock_tenant_org_write(
        db,
        tenant_id,
        (org_id,),
        "invitation_invalid",
    )
    row = await locked_invitation_from_hint(db, digest, org_id, tenant_id)
    return locked.organizations[0], locked.tenant, row
