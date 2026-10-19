"""Company identity predicates live at the shared authorization query boundary."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.sql import Select

from app.models.organization import Tenant, TenantMember, TenantRoleGrant


def active_tenant_membership(tenant_id: UUID, user_id: UUID) -> Select[tuple[Tenant]]:
    return (
        select(Tenant)
        .join(TenantMember, TenantMember.tenant_id == Tenant.id)
        .where(
            Tenant.id == tenant_id,
            TenantMember.user_id == user_id,
            TenantMember.status == "active",
        )
        .execution_options(populate_existing=True)
    )


def tenant_role_query(tenant_id: UUID, user_id: UUID) -> Select[tuple[str]]:
    return select(TenantRoleGrant.role).where(
        TenantRoleGrant.tenant_id == tenant_id, TenantRoleGrant.user_id == user_id
    )
