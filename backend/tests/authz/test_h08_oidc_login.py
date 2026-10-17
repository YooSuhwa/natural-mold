"""H-08: OIDC login with a mock IdP — JIT, group → role mapping, linking (plan 10.10)."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.organization import OrganizationMember, OrganizationRoleGrant, TenantMember
from app.models.organization_identity import UserIdentity
from app.models.user import User
from tests.authz.d04_support import OTHER_ORG_ID, OTHER_USER_ID, seed_scope
from tests.authz.h08_flows import audit_actions, idp_installed, oidc_login, sso_error_of
from tests.authz.h08_support import (
    DOMAIN,
    ISSUER,
    MockIdp,
    make_default_org,
    seed_connection,
    verified_domain,
)
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TestSession


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession):
    await seed_scope(db)
    await make_default_org(db)
    await verified_domain(db)


async def _user(email: str) -> User | None:
    async with TestSession() as db:
        return await db.scalar(select(User).where(User.email == email))


async def test_jit_login_signs_in_and_applies_group_mapping(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    mapping = {"eng": {"org_id": str(OTHER_ORG_ID), "role": "builder"}}
    connection = await seed_connection(db, jit_enabled=True, group_role_mapping=mapping)
    with idp_installed(MockIdp(claims={"groups": ["eng"]})) as idp:
        response = await oidc_login(raw_client, idp, connection)
    assert response.status_code == 302, response.text
    assert response.headers["location"] == settings.cors_origins_list[0] + "/"
    assert settings.cookie_name_access in response.cookies
    form = idp.token_requests[0]
    assert form["code_verifier"] and form["grant_type"] == ["authorization_code"]
    user = await _user(f"alice@{DOMAIN}")
    assert user is not None and user.hashed_password is None
    async with TestSession() as fresh:
        assert await fresh.get(TenantMember, (TEST_TENANT_ID, user.id)) is not None
        assert await fresh.get(OrganizationMember, (OTHER_ORG_ID, user.id)) is not None
        assert await fresh.get(OrganizationMember, (TEST_ORG_ID, user.id)) is None
        role = await fresh.scalar(
            select(OrganizationRoleGrant.role).where(
                OrganizationRoleGrant.principal_id == user.id,
                OrganizationRoleGrant.org_id == OTHER_ORG_ID,
            )
        )
        assert role == "builder"
        identity = await fresh.scalar(select(UserIdentity).where(UserIdentity.user_id == user.id))
        assert identity is not None
        assert (identity.issuer, identity.subject, identity.connection_id) == (
            ISSUER,
            "subject-1",
            connection,
        )
    events = await audit_actions("auth.sso_login")
    assert [event.outcome for event in events] == ["success"]


async def test_jit_without_mapping_joins_default_org_and_second_login_reuses_identity(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    connection = await seed_connection(db, jit_enabled=True, default_role="builder")
    with idp_installed(MockIdp()) as idp:
        assert (await oidc_login(raw_client, idp, connection)).status_code == 302
        raw_client.cookies.clear()
        second = await oidc_login(raw_client, idp, connection)
    assert sso_error_of(second) is None
    user = await _user(f"alice@{DOMAIN}")
    assert user is not None
    async with TestSession() as fresh:
        assert await fresh.get(OrganizationMember, (TEST_ORG_ID, user.id)) is not None
        identities = await fresh.scalars(
            select(UserIdentity).where(UserIdentity.user_id == user.id)
        )
        assert len(list(identities)) == 1
        grants = await fresh.scalars(
            select(OrganizationRoleGrant.role).where(OrganizationRoleGrant.principal_id == user.id)
        )
        assert list(grants) == ["builder"]


async def test_existing_member_on_verified_domain_is_linked_without_jit(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    await verified_domain(db, "test.com")
    connection = await seed_connection(db)
    with idp_installed(MockIdp(claims={"email": "other@test.com"})) as idp:
        response = await oidc_login(raw_client, idp, connection)
    assert sso_error_of(response) is None
    async with TestSession() as fresh:
        identity = await fresh.scalar(select(UserIdentity))
        assert identity is not None and identity.user_id == OTHER_USER_ID


async def test_platform_operator_account_is_never_auto_linked(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    # test@test.com is the fixture's super user and an active member of the company.
    await verified_domain(db, "test.com")
    connection = await seed_connection(db, jit_enabled=True)
    with idp_installed(MockIdp(claims={"email": "test@test.com"})) as idp:
        response = await oidc_login(raw_client, idp, connection)
    assert sso_error_of(response) == "sso_not_invited"
    async with TestSession() as fresh:
        assert await fresh.scalar(select(UserIdentity)) is None


async def test_unknown_user_without_jit_is_not_invited(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    connection = await seed_connection(db, jit_enabled=False)
    with idp_installed(MockIdp()) as idp:
        response = await oidc_login(raw_client, idp, connection)
    assert sso_error_of(response) == "sso_not_invited"
    assert await _user(f"alice@{DOMAIN}") is None
    assert settings.cookie_name_access not in response.cookies


async def test_unverified_domain_account_is_never_auto_linked(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    # other@test.com exists, but test.com is not a verified domain of this company.
    connection = await seed_connection(db, jit_enabled=True)
    with idp_installed(MockIdp(claims={"email": "other@test.com"})) as idp:
        response = await oidc_login(raw_client, idp, connection)
    assert sso_error_of(response) == "sso_domain_unverified"
    async with TestSession() as fresh:
        assert await fresh.scalar(select(UserIdentity)) is None
    events = await audit_actions("auth.sso_login")
    assert [(e.outcome, e.reason_code) for e in events] == [("failure", "sso_domain_unverified")]


async def test_email_verified_false_is_refused(raw_client: AsyncClient, db: AsyncSession) -> None:
    connection = await seed_connection(db, jit_enabled=True)
    with idp_installed(MockIdp(claims={"email_verified": False})) as idp:
        response = await oidc_login(raw_client, idp, connection)
    assert sso_error_of(response) == "sso_email_missing"
    assert await _user(f"alice@{DOMAIN}") is None
