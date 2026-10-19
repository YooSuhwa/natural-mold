"""Registration serializes first-account bootstrap on the migrated default org row."""

from dataclasses import dataclass
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.authz.invitation_queries import invitation_scope_hint, locked_invitation_from_hint
from app.authz.tenant_org_write_locks import (
    TenantOrgWriteTarget,
    lock_tenant_org_write,
    lock_tenant_org_writes,
)
from app.models.organization import Organization, Tenant
from app.models.organization_group import OrgInvitation

DEFAULT_COMPANY_ID: Final = uuid5(NAMESPACE_URL, "moldy/default-tenant")


@dataclass(frozen=True, slots=True)
class RegistrationOrganization:
    organization: Organization
    tenant: Tenant


@dataclass(frozen=True, slots=True)
class RegistrationWriteScope:
    default: RegistrationOrganization
    target: RegistrationOrganization
    invitation: OrgInvitation | None


async def default_organization_hint(db: AsyncSession) -> UUID:
    organization_id = await db.scalar(
        select(Organization.id).where(
            Organization.tenant_id == DEFAULT_COMPANY_ID,
            Organization.is_default.is_(True),
        )
    )
    if organization_id is None:
        raise authorization_error("organization_bootstrap_required")
    return organization_id


async def locked_default_organization(db: AsyncSession) -> RegistrationOrganization:
    organization_id = await default_organization_hint(db)
    locked = await lock_tenant_org_write(
        db,
        DEFAULT_COMPANY_ID,
        (organization_id,),
        "organization_bootstrap_required",
    )
    return RegistrationOrganization(locked.organizations[0], locked.tenant)


async def locked_registration_scope(
    db: AsyncSession, invitation_digest: str | None, include_default: bool
) -> RegistrationWriteScope:
    """Lock every possible signup Tenant before any Organization or invitation."""
    default_id = (
        await default_organization_hint(db)
        if invitation_digest is None or include_default
        else None
    )
    invitation_hint = (
        await invitation_scope_hint(db, invitation_digest)
        if invitation_digest is not None
        else None
    )
    targets: list[TenantOrgWriteTarget] = []
    if default_id is not None:
        targets.append(
            TenantOrgWriteTarget(
                DEFAULT_COMPANY_ID,
                (default_id,),
                "organization_bootstrap_required",
            )
        )
    if invitation_hint is not None:
        invitation_org_id, invitation_tenant_id = invitation_hint
        targets.append(
            TenantOrgWriteTarget(
                invitation_tenant_id,
                (invitation_org_id,),
                "invitation_invalid",
            )
        )
    locked = await lock_tenant_org_writes(db, targets)
    tenants = {tenant.id: tenant for tenant in locked.tenants}
    organizations = {organization.id: organization for organization in locked.organizations}
    invitation = None
    if invitation_hint is not None and invitation_digest is not None:
        invitation_org_id, invitation_tenant_id = invitation_hint
        invitation = await locked_invitation_from_hint(
            db,
            invitation_digest,
            invitation_org_id,
            invitation_tenant_id,
        )
        target = RegistrationOrganization(
            organizations[invitation_org_id],
            tenants[invitation_tenant_id],
        )
    else:
        if default_id is None:
            raise authorization_error("organization_bootstrap_required")
        target = RegistrationOrganization(
            organizations[default_id],
            tenants[DEFAULT_COMPANY_ID],
        )
    default = (
        RegistrationOrganization(
            organizations[default_id],
            tenants[DEFAULT_COMPANY_ID],
        )
        if default_id is not None
        else target
    )
    return RegistrationWriteScope(default, target, invitation)


async def locked_registration_company(db: AsyncSession, tenant_id: UUID) -> Tenant:
    locked = await lock_tenant_org_write(
        db,
        tenant_id,
        (),
        "organization_bootstrap_required",
    )
    return locked.tenant
