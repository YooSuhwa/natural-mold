"""Fresh path-bound company governance authority."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrganizationActor
from app.exceptions import AppError
from app.models.organization import Tenant
from app.tenancy.context import TenantContext, resolve_tenant_context
from app.tenancy.permissions import (
    TenantPermission,
    require_tenant_permission,
    tenant_permissions,
)

TenantAccess = Literal["member", "auditor", "admin"]


@dataclass(frozen=True, slots=True)
class TenantAdminTarget:
    tenant_id: UUID
    permission: TenantPermission | None = None


@dataclass(frozen=True, slots=True)
class TenantAdminContext:
    tenant: Tenant
    actor: OrganizationActor
    access: TenantAccess
    permissions: tuple[TenantPermission, ...]


async def load_tenant_admin_context(
    db: AsyncSession,
    actor: OrganizationActor,
    target: TenantAdminTarget,
) -> TenantAdminContext:
    """Resolve current company authority and lock the Tenant before write authorization."""
    company = await resolve_tenant_context(db, actor, target.tenant_id)
    query = select(Tenant).where(Tenant.id == target.tenant_id)
    if target.permission is not None:
        query = query.with_for_update()
    tenant = await db.scalar(query.execution_options(populate_existing=True))
    if tenant is None:
        raise AppError(code="not_found", message="회사를 찾을 수 없습니다", status=404)
    if target.permission is not None:
        company = await resolve_tenant_context(db, actor, target.tenant_id)
        require_tenant_permission(company, target.permission)
    permissions = tenant_permissions(company)
    return TenantAdminContext(
        tenant=tenant,
        actor=actor,
        access=_tenant_access(company),
        permissions=permissions,
    )


def _tenant_access(context: TenantContext) -> TenantAccess:
    if "admin" in context.roles:
        return "admin"
    if "auditor" in context.roles:
        return "auditor"
    return "member"
