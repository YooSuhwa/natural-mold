"""Explicit reserved development fixture scope, never a production scope fallback."""

from typing import Final
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.tenant_scoping import tenant_role_query
from app.database import is_postgres
from app.exceptions import AppError
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.user import User

E2E_TENANT_ID: Final = uuid5(NAMESPACE_URL, "moldy/e2e-tenant")
E2E_ORG_ID: Final = uuid5(NAMESPACE_URL, "moldy/e2e-organization")
E2E_SEED_LOCK: Final = 0x4D4F4C4459453245


async def lock_e2e_seed(db: AsyncSession) -> None:
    """Serialize configured account/scope bootstrap across development backends."""
    if is_postgres(db):
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": E2E_SEED_LOCK})


def unavailable_scope() -> AppError:
    return AppError(
        code="e2e_scope_unavailable", message="E2E 기본 조직을 사용할 수 없습니다", status=503
    )


async def seed_e2e_scope(db: AsyncSession, user: User) -> None:
    """Enroll the development fixture explicitly without reopening suspended scope."""
    tenant = await db.get(Tenant, E2E_TENANT_ID)
    if tenant is None:
        tenant = Tenant(
            id=E2E_TENANT_ID,
            name="E2E Company",
            slug="moldy-e2e",
            secrets_namespace=f"tenants/{E2E_TENANT_ID}",
        )
        db.add(tenant)
        await db.flush()
    if tenant.status != "active":
        raise unavailable_scope()
    org = await db.get(Organization, E2E_ORG_ID)
    if org is None:
        org = Organization(
            id=E2E_ORG_ID,
            tenant_id=E2E_TENANT_ID,
            name="E2E Organization",
            slug="e2e",
            is_default=True,
        )
        db.add(org)
        await db.flush()
    if org.tenant_id != E2E_TENANT_ID or org.status != "active":
        raise unavailable_scope()
    company_member = await db.get(TenantMember, (E2E_TENANT_ID, user.id))
    if company_member is None:
        db.add(TenantMember(tenant_id=E2E_TENANT_ID, user_id=user.id))
    elif company_member.status != "active":
        raise unavailable_scope()
    org_member = await db.get(OrganizationMember, (E2E_ORG_ID, user.id))
    if org_member is None:
        db.add(OrganizationMember(org_id=E2E_ORG_ID, tenant_id=E2E_TENANT_ID, user_id=user.id))
    elif org_member.tenant_id != E2E_TENANT_ID or org_member.status != "active":
        raise unavailable_scope()
    await db.flush()
    company_admin = await db.scalar(
        tenant_role_query(E2E_TENANT_ID, user.id).where(TenantRoleGrant.role == "admin")
    )
    if company_admin is None:
        db.add(
            TenantRoleGrant(
                tenant_id=E2E_TENANT_ID, user_id=user.id, role="admin", granted_by=user.id
            )
        )
    org_admin = await db.scalar(
        select(OrganizationRoleGrant).where(
            OrganizationRoleGrant.org_id == E2E_ORG_ID,
            OrganizationRoleGrant.principal_type == "user",
            OrganizationRoleGrant.principal_id == user.id,
            OrganizationRoleGrant.role == "admin",
        )
    )
    if org_admin is None:
        db.add(
            OrganizationRoleGrant(
                org_id=E2E_ORG_ID,
                tenant_id=E2E_TENANT_ID,
                principal_type="user",
                principal_id=user.id,
                role="admin",
                granted_by=user.id,
            )
        )
    if user.last_active_org_id is None:
        user.last_active_org_id = E2E_ORG_ID
    await db.flush()
