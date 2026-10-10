"""Token lookup reveals no invitation metadata outside the acceptance service."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.models.organization import Organization, Tenant
from app.models.organization_group import OrgInvitation


async def locked_invitation_scope(
    db: AsyncSession, digest: str
) -> tuple[Organization, Tenant, OrgInvitation]:
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
    org_id, tenant_id = hint
    organization = await db.scalar(
        select(Organization)
        .where(Organization.id == org_id, Organization.tenant_id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    tenant = await db.scalar(
        select(Tenant)
        .where(Tenant.id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
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
    if organization is None or tenant is None or row is None:
        raise authorization_error("invitation_invalid")
    return organization, tenant, row
