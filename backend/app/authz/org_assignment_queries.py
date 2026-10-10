"""Organization/company pairs scope all assignment reads and delta replacements."""

from sqlalchemy import select
from sqlalchemy.sql import Select

from app.authz.org_control import OrgControlContext
from app.models.organization import OrganizationRoleGrant
from app.models.organization_group import OrgCapabilityGrant


def role_query(context: OrgControlContext) -> Select[tuple[OrganizationRoleGrant]]:
    return select(OrganizationRoleGrant).where(
        OrganizationRoleGrant.org_id == context.organization.id,
        OrganizationRoleGrant.tenant_id == context.organization.tenant_id,
    )


def capability_query(context: OrgControlContext) -> Select[tuple[OrgCapabilityGrant]]:
    return select(OrgCapabilityGrant).where(
        OrgCapabilityGrant.org_id == context.organization.id,
        OrgCapabilityGrant.tenant_id == context.organization.tenant_id,
    )
