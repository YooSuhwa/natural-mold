"""Organization oversight reads: ``can_view_audit`` / ``can_view_usage`` (plan 4.x, 7.1, 14.1).

``define can_view_audit: auditor or admin or can_view_tenant_audit from tenant`` (and the
usage twin). Company admins/auditors and org admin/auditor role holders (direct or via a
group, membership not required) pass; a missing/foreign organization or a non-member
with neither gets 404; a member without the permission gets 403 ``permission_required``.
Oversight never grants resource contents.
"""

from typing import Final, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.authz.org_context import OrganizationActor
from app.authz.org_permissions import organization_permissions
from app.exceptions import AppError
from app.models.organization import Organization, OrganizationMember
from app.tenancy.context import resolve_tenant_context
from app.tenancy.permissions import TenantPermission, tenant_permissions

OrgOversightPermission = Literal["can_view_audit", "can_view_usage"]
_COMPANY_PERMISSION: Final[dict[OrgOversightPermission, TenantPermission]] = {
    "can_view_audit": "can_view_tenant_audit",
    "can_view_usage": "can_view_tenant_usage",
}
_ORG_ROLES: Final = frozenset({"admin", "auditor"})


async def load_org_oversight(
    db: AsyncSession, actor: OrganizationActor, org_id: UUID, permission: OrgOversightPermission
) -> Organization:
    with db.no_autoflush:
        organization = await db.scalar(
            select(Organization)
            .where(Organization.id == org_id)
            .execution_options(populate_existing=True)
        )
        if organization is None:
            raise authorization_error("not_found")
        try:
            company = await resolve_tenant_context(db, actor, organization.tenant_id)
        except AppError as error:
            if error.status == 404:
                raise authorization_error("not_found") from error
            raise
        company_oversight = _COMPANY_PERMISSION[permission] in tenant_permissions(company)
        if company_oversight:
            return organization
        roles, _ = await organization_permissions(db, organization.id, actor.id)
        if not _ORG_ROLES.isdisjoint(roles):
            return organization  # org admins/auditors, also granted without membership
        member = await db.scalar(
            select(OrganizationMember.user_id).where(
                OrganizationMember.org_id == organization.id,
                OrganizationMember.tenant_id == organization.tenant_id,
                OrganizationMember.user_id == actor.id,
                OrganizationMember.status == "active",
            )
        )
        if member is None:
            raise authorization_error("not_found")
        raise authorization_error("permission_required")
