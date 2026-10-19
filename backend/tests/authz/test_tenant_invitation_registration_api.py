"""Public registration through a company-only invitation."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.authz.registration_queries import DEFAULT_COMPANY_ID
from app.config import settings
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.organization_group import GroupMember, OrgCapabilityGrant
from app.models.user import User
from tests.authz.tenant_invitation_fixture import seed_tenant_invitation

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_first_company_signup_serializes_on_bootstrap_tenant_without_default_org(
    db: AsyncSession, raw_client: AsyncClient
) -> None:
    await db.execute(delete(Organization).where(Organization.tenant_id == DEFAULT_COMPANY_ID))
    tenant_id = uuid4()
    db.add(
        Tenant(
            id=tenant_id,
            name="First Company Signup",
            slug=f"first-company-{tenant_id.hex[:8]}",
            secrets_namespace=f"tenants/{tenant_id}",
        )
    )
    await db.commit()
    _, token = await seed_tenant_invitation(
        db,
        tenant_id=tenant_id,
        created_by=None,
        email="first-company-signup@example.com",
    )
    with pytest.MonkeyPatch.context() as options:
        options.setattr(settings, "allow_first_user_as_admin", True)
        options.setattr(settings, "registration_policy", "invite_only")
        result = await raw_client.post(
            "/api/auth/register",
            json={
                "email": "first-company-signup@example.com",
                "password": "correct horse",
                "name": "First Company",
                "invitation_token": token,
            },
        )
    assert result.status_code == 201
    user_id = UUID(result.json()["user"]["id"])
    assert result.json()["user"]["is_super_user"] is True
    assert ACTIVE_ORG_COOKIE not in result.cookies
    user = await db.get(User, user_id)
    assert user is not None and user.last_active_org_id is None
    assert await db.get(TenantMember, (tenant_id, user_id)) is not None
    assert (
        await db.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(OrganizationMember.user_id == user_id)
        )
        == 0
    )


async def test_company_signup_needs_no_default_org_and_grants_no_org_scope(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    await db.execute(delete(Organization).where(Organization.tenant_id == DEFAULT_COMPANY_ID))
    tenant = await db.get(Tenant, org_fixture.tenant)
    assert tenant is not None
    tenant.settings = {"registration_policy": "invite_only"}
    invitation, token = await seed_tenant_invitation(
        db,
        tenant_id=tenant.id,
        created_by=org_fixture.user.id,
        email="company-signup@example.com",
        role="auditor",
    )
    with pytest.MonkeyPatch.context() as options:
        options.setattr(settings, "allow_first_user_as_admin", True)
        options.setattr(settings, "registration_policy", "invite_only")
        result = await raw_client.post(
            "/api/auth/register",
            json={
                "email": "company-signup@example.com",
                "password": "correct horse",
                "name": "Company Signup",
                "invitation_token": token,
            },
        )
    assert result.status_code == 201
    user_id = UUID(result.json()["user"]["id"])
    user = await db.get(User, user_id)
    assert user is not None and user.last_active_org_id is None
    assert ACTIVE_ORG_COOKIE not in result.cookies
    assert await db.get(TenantMember, (tenant.id, user_id)) is not None
    role = await db.scalar(
        select(TenantRoleGrant.role).where(
            TenantRoleGrant.tenant_id == tenant.id, TenantRoleGrant.user_id == user_id
        )
    )
    assert role == "auditor"
    assert (
        await db.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(OrganizationMember.user_id == user_id)
        )
        == 0
    )
    assert (
        await db.scalar(
            select(func.count())
            .select_from(OrganizationRoleGrant)
            .where(OrganizationRoleGrant.principal_id == user_id)
        )
        == 0
    )
    assert (
        await db.scalar(
            select(func.count()).select_from(GroupMember).where(GroupMember.user_id == user_id)
        )
        == 0
    )
    assert (
        await db.scalar(
            select(func.count())
            .select_from(OrgCapabilityGrant)
            .where(OrgCapabilityGrant.principal_id == user_id)
        )
        == 0
    )
    await db.refresh(invitation)
    assert invitation.status == "accepted" and invitation.accepted_by == user_id


async def test_missing_bootstrap_tenant_fails_before_company_user_creation(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    invitation, token = await seed_tenant_invitation(
        db,
        tenant_id=org_fixture.tenant,
        created_by=org_fixture.user.id,
        email="missing-bootstrap@example.com",
    )
    bootstrap = await db.get(Tenant, DEFAULT_COMPANY_ID)
    assert bootstrap is not None
    await db.delete(bootstrap)
    await db.commit()
    with pytest.MonkeyPatch.context() as options:
        options.setattr(settings, "allow_first_user_as_admin", True)
        result = await raw_client.post(
            "/api/auth/register",
            json={
                "email": "missing-bootstrap@example.com",
                "password": "correct horse",
                "name": "Missing Bootstrap",
                "invitation_token": token,
            },
        )
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "organization_bootstrap_required"
    assert (
        await db.scalar(select(User.id).where(User.email == "missing-bootstrap@example.com"))
        is None
    )
    await db.refresh(invitation)
    assert invitation.status == "pending"


async def test_company_policy_and_email_are_checked_before_user_creation(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    tenant = await db.get(Tenant, org_fixture.tenant)
    assert tenant is not None
    invitation, token = await seed_tenant_invitation(
        db,
        tenant_id=tenant.id,
        created_by=org_fixture.user.id,
        email="bound-company@example.com",
    )
    wrong = await raw_client.post(
        "/api/auth/register",
        json={
            "email": "other-company@example.com",
            "password": "correct horse",
            "name": "Wrong Email",
            "invitation_token": token,
        },
    )
    assert wrong.status_code == 403
    tenant.settings = {"registration_policy": "closed"}
    await db.commit()
    closed = await raw_client.post(
        "/api/auth/register",
        json={
            "email": "bound-company@example.com",
            "password": "correct horse",
            "name": "Closed Company",
            "invitation_token": token,
        },
    )
    assert closed.status_code == 403
    assert (
        await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.email.in_(["other-company@example.com", "bound-company@example.com"]))
        )
        == 0
    )
    await db.refresh(invitation)
    assert invitation.status == "pending"
