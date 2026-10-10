"""Fresh company membership and status checks, independent of platform authority."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrganizationActor, OrgContext, active_memberships
from app.authz.tenant_scoping import active_tenant_membership, tenant_role_query
from app.exceptions import AppError
from app.models.organization import Organization, Tenant

TenantRole = Literal["admin", "auditor"]
ROLE_ADAPTER = TypeAdapter(tuple[TenantRole, ...])


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: UUID
    user_id: UUID
    roles: tuple[TenantRole, ...] = ()


def ensure_tenant_active(tenant: Tenant) -> None:
    if tenant.status == "suspended":
        raise AppError(code="tenant_suspended", message="회사가 정지되었습니다", status=403)
    if tenant.status != "active":
        raise AppError(code="tenant_unavailable", message="사용할 수 없는 회사입니다", status=403)


async def resolve_tenant_context(
    db: AsyncSession, actor: OrganizationActor, tenant_id: UUID
) -> TenantContext:
    """A foreign/missing company is identical; super_user grants no company access."""
    tenant = await db.scalar(active_tenant_membership(tenant_id, actor.id))
    if tenant is None:
        raise AppError(code="not_found", message="회사를 찾을 수 없습니다", status=404)
    ensure_tenant_active(tenant)
    roles = await tenant_roles(db, tenant_id, actor.id)
    return TenantContext(tenant_id=tenant.id, user_id=actor.id, roles=roles)


async def tenant_roles(db: AsyncSession, tenant_id: UUID, user_id: UUID) -> tuple[TenantRole, ...]:
    roles = ROLE_ADAPTER.validate_python(
        tuple((await db.scalars(tenant_role_query(tenant_id, user_id))).all())
    )
    return tuple(sorted(set(roles)))


async def tenant_context_from_org(db: AsyncSession, context: OrgContext) -> TenantContext:
    """Verify the org/company pair again; immutable contexts are never authority receipts."""
    membership = (
        await db.execute(
            active_memberships(context.user_id)
            .where(Organization.id == context.org_id, Organization.tenant_id == context.tenant_id)
            .execution_options(populate_existing=True)
        )
    ).first()
    if membership is None:
        raise AppError(code="forbidden", message="권한이 없습니다", status=403)
    organization, tenant = membership
    ensure_tenant_active(tenant)
    if organization.status != "active":
        raise AppError(
            code="organization_unavailable", message="사용할 수 없는 조직입니다", status=403
        )
    roles = await tenant_roles(db, tenant.id, context.user_id)
    return TenantContext(tenant_id=tenant.id, user_id=context.user_id, roles=roles)
