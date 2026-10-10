"""Registration serializes first-account bootstrap on the migrated default org row."""

from dataclasses import dataclass
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.models.organization import Organization, Tenant

DEFAULT_COMPANY_ID: Final = uuid5(NAMESPACE_URL, "moldy/default-tenant")


@dataclass(frozen=True, slots=True)
class RegistrationOrganization:
    organization: Organization
    tenant: Tenant


async def locked_default_organization(db: AsyncSession) -> RegistrationOrganization:
    row = (
        (
            await db.execute(
                select(Organization, Tenant)
                .join(Tenant, Tenant.id == Organization.tenant_id)
                .where(Tenant.id == DEFAULT_COMPANY_ID, Organization.is_default.is_(True))
                .with_for_update(of=Organization)
                .execution_options(populate_existing=True)
            )
        )
        .tuples()
        .one_or_none()
    )
    if row is None:
        raise authorization_error("organization_bootstrap_required")
    return RegistrationOrganization(*row)


async def locked_registration_company(db: AsyncSession, tenant_id: UUID) -> Tenant:
    row = await db.scalar(
        select(Tenant)
        .where(Tenant.id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise authorization_error("organization_bootstrap_required")
    return row
