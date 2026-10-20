"""REGISTRATION_POLICY=personal_tenant gives each uninvited account its own company."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.authz.config import AuthzSettings
from app.config import settings
from app.models.audit_event import AuditEvent
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRoleGrant,
    Tenant,
    TenantMember,
    TenantRoleGrant,
)
from app.models.organization_group import OrgInvitation
from tests.authz.registration_fixture import seed_registration_default
from tests.authz.test_org_groups_api import grant_admin

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


@pytest.fixture
def personal_signup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "deployment_mode", "multi_tenant")
    monkeypatch.setattr(settings, "registration_policy", "personal_tenant")
    monkeypatch.setattr(settings, "allow_first_user_as_admin", False)


async def _register(client: AsyncClient, email: str, **extra: str) -> UUID:
    response = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "correct horse", "name": "Person", **extra},
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["user"]["id"])


async def _companies(db: AsyncSession, user_id: UUID) -> list[UUID]:
    rows = await db.scalars(select(TenantMember.tenant_id).where(TenantMember.user_id == user_id))
    return list(rows.all())


@pytest.mark.usefixtures("personal_signup")
async def test_uninvited_signup_creates_and_administers_its_own_company(
    db: AsyncSession, raw_client: AsyncClient
) -> None:
    # Given: an installation that gives self-service signups their own company.
    default = await seed_registration_default(db)
    # When: an uninvited account is created.
    response = await raw_client.post(
        "/api/auth/register",
        json={"email": "solo@example.com", "password": "correct horse", "name": "Solo"},
    )
    # Then: it belongs only to a new personal company and administers it and its org.
    assert response.status_code == 201
    user_id = UUID(response.json()["user"]["id"])
    assert response.json()["user"]["is_super_user"] is False
    [company_id] = await _companies(db, user_id)
    assert company_id != default.tenant_id
    company = await db.get(Tenant, company_id)
    assert company is not None
    assert company.settings == {"kind": "personal"}
    assert company.slug.startswith("personal-")
    assert company.name == "Solo의 워크스페이스"
    organization = await db.scalar(select(Organization).where(Organization.tenant_id == company_id))
    assert organization is not None and organization.is_default
    assert response.cookies[ACTIVE_ORG_COOKIE] == str(organization.id)
    assert await db.get(OrganizationMember, (organization.id, user_id)) is not None
    org_role = await db.scalar(
        select(OrganizationRoleGrant.role).where(OrganizationRoleGrant.principal_id == user_id)
    )
    company_role = await db.scalar(
        select(TenantRoleGrant.role).where(TenantRoleGrant.user_id == user_id)
    )
    assert org_role == "admin" and company_role == "admin"
    created = await db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "tenant.create", AuditEvent.actor_user_id == user_id
        )
    )
    assert created is not None and created.target_id == str(company_id)
    assert created.event_metadata is not None
    assert created.event_metadata["kind"] == "personal"


@pytest.mark.usefixtures("personal_signup")
async def test_each_personal_signup_gets_a_separate_company(
    db: AsyncSession, raw_client: AsyncClient
) -> None:
    # Given: personal-company signup.
    await seed_registration_default(db)
    # When: two strangers sign up.
    first = await _register(raw_client, "first-person@example.com")
    second = await _register(raw_client, "second-person@example.com")
    # Then: neither shares a company with the other.
    assert set(await _companies(db, first)).isdisjoint(await _companies(db, second))


async def test_first_account_still_administers_the_default_company(
    db: AsyncSession,
    raw_client: AsyncClient,
    personal_signup: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: personal-company signup with first-user bootstrap enabled.
    default = await seed_registration_default(db)
    monkeypatch.setattr(settings, "allow_first_user_as_admin", True)
    # When: the installation's first account is created.
    user_id = await _register(raw_client, "operator@example.com")
    # Then: it is the operator in the default company and no personal company exists.
    assert await _companies(db, user_id) == [default.tenant_id]
    personal = await db.scalar(select(Tenant.id).where(Tenant.slug.startswith("personal-")))
    assert personal is None


@pytest.mark.usefixtures("personal_signup")
async def test_personal_company_takes_only_the_configured_default_limits(
    db: AsyncSession, raw_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: installation-wide defaults for new companies.
    await seed_registration_default(db)
    monkeypatch.setattr(
        settings,
        "tenant_default_limits",
        '{"member_limit": 5, "monthly_spend_limit": 20, "request_rate_limit": 30}',
    )
    # When: a personal company is created.
    user_id = await _register(raw_client, "limited@example.com")
    # Then: the set keys are stored; absent keys stay absent (unlimited/allowed).
    [company_id] = await _companies(db, user_id)
    company = await db.get(Tenant, company_id)
    assert company is not None
    assert company.limits == {
        "member_limit": 5,
        "monthly_spend_limit": "20",
        "request_rate_limit": 30,
    }


@pytest.mark.usefixtures("personal_signup")
async def test_invited_signup_still_joins_the_inviting_organization(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    # Given: an organization invitation under personal-company signup.
    await seed_registration_default(db)
    await grant_admin(db, org_fixture)
    created = await raw_client.post(
        f"/api/orgs/{org_fixture.first}/invitations",
        json={"email": "invited-person@example.com", "role": "builder"},
        headers=org_fixture.session.headers(),
    )
    assert created.status_code == 201
    # When: the invited email signs up with the link.
    user_id = await _register(
        raw_client, "invited-person@example.com", invitation_token=created.json()["token"]
    )
    # Then: it joins the inviting organization and no personal company is created.
    row = await db.get(OrgInvitation, UUID(created.json()["id"]))
    assert row is not None and row.accepted_by == user_id
    memberships = await db.scalars(
        select(OrganizationMember.org_id).where(OrganizationMember.user_id == user_id)
    )
    assert list(memberships.all()) == [org_fixture.first]
    inviting = await db.scalar(
        select(Organization.tenant_id).where(Organization.id == org_fixture.first)
    )
    assert await _companies(db, user_id) == [inviting]


def test_personal_signup_requires_a_multi_tenant_installation() -> None:
    # single_tenant shares storage and lets company admins manage their own limits.
    with pytest.raises(ValidationError, match="requires multi_tenant"):
        AuthzSettings(registration_policy="personal_tenant", deployment_mode="single_tenant")
    allowed = AuthzSettings(registration_policy="personal_tenant", deployment_mode="multi_tenant")
    assert allowed.registration_policy == "personal_tenant"


@pytest.mark.parametrize(
    "limits",
    ['{"seat_limit": 1}', '{"member_limit": -1}', "not json", '{"allow_system_models": 2}'],
)
def test_personal_signup_refuses_to_start_with_unusable_default_limits(limits: str) -> None:
    # Otherwise every personal signup would fail at the first request.
    with pytest.raises(ValidationError):
        AuthzSettings(
            registration_policy="personal_tenant",
            deployment_mode="multi_tenant",
            tenant_default_limits=limits,
        )
