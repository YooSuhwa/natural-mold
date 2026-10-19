"""Unit auth fixtures represent the default company/org seeded by actual m82."""

from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.migrations.org_authz_v1.contracts import ORG, TENANT
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.user import User


async def seed_registration_default(db: AsyncSession) -> Organization:
    tenant = await db.get(Tenant, TENANT)
    if tenant is None:
        db.add(
            Tenant(
                id=TENANT,
                name="기본 회사",
                slug="moldy-default",
                secrets_namespace=f"tenants/{TENANT}",
            )
        )
        await db.flush()
    organization = await db.get(Organization, ORG)
    if organization is None:
        organization = Organization(
            id=ORG,
            tenant_id=TENANT,
            name="기본 조직",
            slug="default",
            is_default=True,
            settings={"default_member_role": "member"},
        )
        db.add(organization)
    await db.commit()
    return organization


async def clear_default_membership(db: AsyncSession, user_id: UUID) -> None:
    await db.execute(
        delete(OrganizationRoleGrant).where(
            OrganizationRoleGrant.org_id == ORG,
            OrganizationRoleGrant.principal_id == user_id,
            OrganizationRoleGrant.principal_type == "user",
        )
    )
    await db.execute(
        delete(TenantRoleGrant).where(
            TenantRoleGrant.tenant_id == TENANT,
            TenantRoleGrant.user_id == user_id,
        )
    )
    await db.execute(
        delete(OrganizationMember).where(
            OrganizationMember.org_id == ORG,
            OrganizationMember.user_id == user_id,
        )
    )
    await db.execute(
        delete(TenantMember).where(
            TenantMember.tenant_id == TENANT, TenantMember.user_id == user_id
        )
    )
    user = await db.get(User, user_id)
    assert user is not None
    user.last_active_org_id = None
    await db.commit()
