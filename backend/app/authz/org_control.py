"""Organization governance checks do not grant resource content access."""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.authz.org_context import OrganizationActor
from app.authz.org_permissions import organization_permissions
from app.authz.tenant_org_write_locks import lock_tenant_org_write
from app.exceptions import AppError
from app.models.organization import Organization, OrganizationMember
from app.tenancy.context import resolve_tenant_context

OrgControlPermission = Literal["can_manage_members", "can_manage_policy"]


class OrgControlActor(OrganizationActor, Protocol):
    @property
    def email(self) -> str: ...


@dataclass(frozen=True, slots=True)
class OrgControlTarget:
    org_id: UUID
    permission: OrgControlPermission | None = None


@dataclass(frozen=True, slots=True)
class OrgControlContext:
    organization: Organization
    actor: OrgControlActor
    permissions: tuple[OrgControlPermission, ...]


async def load_org_control(
    db: AsyncSession, actor: OrgControlActor, target: OrgControlTarget
) -> OrgControlContext:
    query = select(Organization).where(Organization.id == target.org_id)
    if target.permission is not None:
        query = query.with_for_update()
    organization = await db.scalar(query.execution_options(populate_existing=True))
    if organization is None:
        raise authorization_error("not_found")
    try:
        company = await resolve_tenant_context(db, actor, organization.tenant_id)
    except AppError as error:
        if error.status == 404:
            raise authorization_error("not_found") from error
        raise
    member = await db.scalar(
        select(OrganizationMember.user_id).where(
            OrganizationMember.org_id == organization.id,
            OrganizationMember.tenant_id == organization.tenant_id,
            OrganizationMember.user_id == actor.id,
            OrganizationMember.status == "active",
        )
    )
    company_admin = "admin" in company.roles
    if member is None and not company_admin:
        raise authorization_error("not_found")
    if organization.status != "active":
        raise authorization_error("organization_unavailable")
    roles, _ = await organization_permissions(db, organization.id, actor.id)
    permissions: tuple[OrgControlPermission, ...] = (
        ("can_manage_members", "can_manage_policy") if company_admin or "admin" in roles else ()
    )
    if target.permission is not None and target.permission not in permissions:
        raise authorization_error("permission_required")
    return OrgControlContext(organization=organization, actor=actor, permissions=permissions)


async def load_org_write_control(
    db: AsyncSession,
    actor: OrgControlActor,
    target: OrgControlTarget,
    tenant_id: UUID,
) -> OrgControlContext:
    """Lock the parent scope before revalidating write authority."""
    await lock_tenant_org_write(db, tenant_id, (target.org_id,))
    return await load_org_control(db, actor, target)
