"""Registration policy applies before account creation and commits membership atomically."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.config import settings
from app.models.audit_event import AuditEvent
from app.models.organization import (
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.organization_group import OrgInvitation
from app.models.refresh_token import RefreshToken
from app.models.user import User
from tests.authz.registration_fixture import seed_registration_default
from tests.authz.test_org_groups_api import grant_admin

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_default_signup_joins_default_org_and_bootstraps_first_administrators(
    db: AsyncSession, raw_client: AsyncClient
) -> None:
    # Given: the installation's migrated default company and org.
    organization = await seed_registration_default(db)
    with pytest.MonkeyPatch.context() as options:
        options.setattr(settings, "allow_first_user_as_admin", True)
        options.setattr(settings, "registration_policy", "default_org")
        # When: the first account is created.
        response = await raw_client.post(
            "/api/auth/register",
            json={
                "email": "first-policy@example.com",
                "password": "correct horse",
                "name": "First",
            },
        )
    # Then: platform authority, default org/company admin grants and cookie commit together.
    assert response.status_code == 201
    user_id = UUID(response.json()["user"]["id"])
    assert response.json()["user"]["is_super_user"] is True
    assert response.cookies[ACTIVE_ORG_COOKIE] == str(organization.id)
    assert await db.get(TenantMember, (organization.tenant_id, user_id)) is not None
    assert await db.get(OrganizationMember, (organization.id, user_id)) is not None
    org_role = await db.scalar(
        select(OrganizationRoleGrant.role).where(OrganizationRoleGrant.principal_id == user_id)
    )
    company_role = await db.scalar(
        select(TenantRoleGrant.role).where(TenantRoleGrant.user_id == user_id)
    )
    assert org_role == "admin" and company_role == "admin"
    grant_events = list(
        (
            await db.scalars(
                select(AuditEvent).where(
                    AuditEvent.actor_user_id == user_id,
                    AuditEvent.action.in_(["org.role_grant", "tenant.role_grant"]),
                )
            )
        ).all()
    )
    targets = {row.action: row.target_id for row in grant_events}
    assert targets == {
        "org.role_grant": str(organization.id),
        "tenant.role_grant": str(organization.tenant_id),
    }


@pytest.mark.parametrize("policy", ["closed", "invite_only"])
async def test_restricted_signup_without_link_creates_no_account_or_session(
    db: AsyncSession, raw_client: AsyncClient, policy: Literal["closed", "invite_only"]
) -> None:
    # Given: an installation whose account signup is restricted.
    await seed_registration_default(db)
    with pytest.MonkeyPatch.context() as options:
        options.setattr(settings, "registration_policy", policy)
        # When: a public caller submits an account without an invitation.
        response = await raw_client.post(
            "/api/auth/register",
            json={
                "email": "denied-policy@example.com",
                "password": "correct horse",
                "name": "Denied",
            },
        )
    # Then: neither the user nor refresh whitelist is created.
    assert response.status_code == 403
    assert await db.scalar(select(User.id).where(User.email == "denied-policy@example.com")) is None
    assert list((await db.scalars(select(RefreshToken))).all()) == []
    assert settings.cookie_name_access not in response.cookies


async def test_invitation_only_signup_consumes_link_and_joins_only_invited_org(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    # Given: an explicit invitation issued by another company's organization admin.
    await seed_registration_default(db)
    await grant_admin(db, org_fixture)
    created = await raw_client.post(
        f"/api/orgs/{org_fixture.first}/invitations",
        json={"email": "new-invited@example.com", "role": "builder"},
        headers=org_fixture.session.headers(),
    )
    assert created.status_code == 201
    with pytest.MonkeyPatch.context() as options:
        options.setattr(settings, "registration_policy", "invite_only")
        # When: that email creates an account with the link's secret.
        result = await raw_client.post(
            "/api/auth/register",
            json={
                "email": "new-invited@example.com",
                "password": "correct horse",
                "name": "Invited",
                "invitation_token": created.json()["token"],
            },
        )
    # Then: account and invitation commit together, without default-company enrollment.
    assert result.status_code == 201
    user_id = UUID(result.json()["user"]["id"])
    row = await db.get(OrgInvitation, UUID(created.json()["id"]))
    assert row is not None and row.accepted_by == user_id
    memberships = list(
        (
            await db.scalars(
                select(OrganizationMember.org_id).where(OrganizationMember.user_id == user_id)
            )
        ).all()
    )
    assert memberships == [org_fixture.first]
    assert result.cookies[ACTIVE_ORG_COOKIE] == str(org_fixture.first)


async def test_invalid_signup_link_rolls_back_account_creation(
    db: AsyncSession, raw_client: AsyncClient
) -> None:
    # Given: an invitation-only installation and an unknown link.
    await seed_registration_default(db)
    with pytest.MonkeyPatch.context() as options:
        options.setattr(settings, "registration_policy", "invite_only")
        # When: a caller tries to create an account using that link.
        result = await raw_client.post(
            "/api/auth/register",
            json={
                "email": "invalid-link@example.com",
                "password": "correct horse",
                "name": "Invalid",
                "invitation_token": "unknown-link",
            },
        )
    # Then: the invalid token does not leave behind an unscoped account.
    assert result.status_code == 404
    assert await db.scalar(select(User.id).where(User.email == "invalid-link@example.com")) is None


async def test_default_company_identity_survives_a_slug_rename(
    db: AsyncSession, raw_client: AsyncClient
) -> None:
    # Given: the migrated default company's display/routing slug has changed.
    organization = await seed_registration_default(db)
    tenant = await db.get(Tenant, organization.tenant_id)
    assert tenant is not None
    tenant.slug = "renamed-default"
    await db.commit()
    # When: a public account is created under default-org policy.
    result = await raw_client.post(
        "/api/auth/register",
        json={
            "email": "renamed-default@example.com",
            "password": "correct horse",
            "name": "Renamed",
        },
    )
    # Then: stable company identity, rather than its mutable slug, determines enrollment.
    assert result.status_code == 201
    user_id = UUID(result.json()["user"]["id"])
    assert await db.get(TenantMember, (tenant.id, user_id)) is not None


async def test_missing_default_refuses_signup_instead_of_creating_an_unscoped_account(
    db: AsyncSession, raw_client: AsyncClient
) -> None:
    # Given: installation bootstrap is incomplete.
    organization = await seed_registration_default(db)
    await db.delete(organization)
    await db.commit()
    # When: a caller attempts public signup.
    result = await raw_client.post(
        "/api/auth/register",
        json={
            "email": "unscoped@example.com",
            "password": "correct horse",
            "name": "Missing",
        },
    )
    # Then: no account/session is created and initialization is explicitly required.
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "organization_bootstrap_required"
    assert await db.scalar(select(User.id).where(User.email == "unscoped@example.com")) is None


async def test_org_cannot_reopen_company_signup(db: AsyncSession, raw_client: AsyncClient) -> None:
    # Given: company signup is closed while its org policy attempts to reopen it.
    organization = await seed_registration_default(db)
    tenant = await db.get(Tenant, organization.tenant_id)
    assert tenant is not None
    tenant.settings = {"registration_policy": "closed"}
    organization.settings = {"registration_policy": "default_org", "default_member_role": "builder"}
    await db.commit()
    with pytest.MonkeyPatch.context() as options:
        options.setattr(settings, "allow_first_user_as_admin", False)
        # When: a public caller submits signup.
        result = await raw_client.post(
            "/api/auth/register",
            json={
                "email": "company-closed@example.com",
                "password": "correct horse",
                "name": "Closed",
            },
        )
    # Then: the stricter company policy wins.
    assert result.status_code == 403
    assert (
        await db.scalar(select(User.id).where(User.email == "company-closed@example.com")) is None
    )
