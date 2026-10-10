"""Two companies, a foreign organization and role sources for context scenarios."""

from dataclasses import dataclass
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.dependencies import CurrentUser
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
)
from app.models.organization_group import Group, GroupMember, OrgCapabilityGrant
from app.models.user import User
from tests.authz.registration_fixture import clear_default_membership
from tests.conftest import AuthSession, register_session


@dataclass(frozen=True, slots=True)
class OrgFixture:
    user: CurrentUser
    session: AuthSession
    first: UUID
    second: UUID
    foreign: UUID
    tenant: UUID


@pytest.fixture(name="org_fixture")
async def org_fixture(raw_client: AsyncClient, db: AsyncSession) -> OrgFixture:
    login = await register_session(raw_client, email="org-context@example.com", name="Organization")
    user = await db.scalar(select(User).where(User.email == "org-context@example.com"))
    assert user is not None
    await clear_default_membership(db, user.id)
    raw_client.cookies.delete(ACTIVE_ORG_COOKIE)
    tenants = [
        Tenant(name=name, slug=name, secrets_namespace=name) for name in ("A", "B", "Foreign")
    ]
    db.add_all(tenants)
    await db.flush()
    organizations = [Organization(tenant_id=t.id, name=t.name, slug=t.slug) for t in tenants]
    db.add_all(organizations)
    await db.flush()
    for tenant, org in zip(tenants[:2], organizations[:2], strict=True):
        db.add(TenantMember(tenant_id=tenant.id, user_id=user.id))
        db.add(OrganizationMember(org_id=org.id, tenant_id=tenant.id, user_id=user.id))
    group = Group(org_id=organizations[0].id, tenant_id=tenants[0].id, name="Builders")
    db.add(group)
    await db.flush()
    db.add(
        GroupMember(
            group_id=group.id,
            user_id=user.id,
            org_id=organizations[0].id,
            tenant_id=tenants[0].id,
        )
    )
    db.add(
        OrganizationRoleGrant(
            org_id=organizations[0].id,
            tenant_id=tenants[0].id,
            role="builder",
            principal_type="group",
            principal_id=group.id,
        )
    )
    db.add(
        OrgCapabilityGrant(
            org_id=organizations[0].id,
            tenant_id=tenants[0].id,
            capability="cap_connect_database",
            principal_type="user",
            principal_id=user.id,
        )
    )
    user.last_active_org_id = organizations[0].id
    await db.commit()
    return OrgFixture(
        user=CurrentUser(
            id=user.id, email=user.email, name=user.name, is_super_user=user.is_super_user
        ),
        session=login,
        first=organizations[0].id,
        second=organizations[1].id,
        foreign=organizations[2].id,
        tenant=tenants[0].id,
    )
