"""H-08 hardening: attaching SSO identities to existing accounts (H1, M1, L1)."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.password import hash_password
from app.auth.sso.domains import normalize_email
from app.config import settings
from app.models.organization import TenantMember
from app.models.organization_identity import UserIdentity
from app.models.refresh_token import RefreshToken
from app.models.user import User
from tests.authz.d04_support import seed_scope
from tests.authz.h08_flows import idp_installed, oidc_login, sso_error_of
from tests.authz.h08_support import (
    DOMAIN,
    MockIdp,
    make_default_org,
    seed_connection,
    verified_domain,
)
from tests.authz.unit_scope_fixture import TEST_TENANT_ID
from tests.conftest import TestSession

BOB = f"bob@{DOMAIN}"
PASSWORD = "bob password 123"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession):
    await seed_scope(db)
    await make_default_org(db)
    await verified_domain(db)


async def _bob(db: AsyncSession, *, verified: bool, password: bool = True) -> UUID:
    user = User(
        id=uuid4(),
        email=BOB,
        name="Bob",
        is_active=True,
        hashed_password=hash_password(PASSWORD) if password else None,
        email_verified_at=datetime.now(UTC) if verified else None,
    )
    db.add(user)
    await db.flush()
    db.add(TenantMember(tenant_id=TEST_TENANT_ID, user_id=user.id))
    await db.commit()
    return user.id


async def _tokens(user_id: UUID) -> list[RefreshToken]:
    async with TestSession() as db:
        rows = await db.scalars(
            select(RefreshToken)
            .where(RefreshToken.user_id == user_id)
            .order_by(RefreshToken.created_at)
        )
        return list(rows)


async def _identities() -> list[UserIdentity]:
    async with TestSession() as db:
        return list(await db.scalars(select(UserIdentity)))


async def _password_login(client: AsyncClient):
    return await client.post("/api/auth/login", json={"email": BOB, "password": PASSWORD})


async def test_unverified_password_account_needs_one_password_login_to_link(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    bob = await _bob(db, verified=False)
    assert (await _password_login(raw_client)).status_code == 200
    connection = await seed_connection(db)
    with idp_installed(MockIdp(claims={"email": BOB})) as idp:
        pending = await oidc_login(raw_client, idp, connection)
    assert pending.headers["location"].endswith("/login?sso_link=pending")
    assert settings.cookie_name_access not in pending.cookies
    assert await _identities() == []
    confirmed = await _password_login(raw_client)
    assert confirmed.status_code == 200, confirmed.text
    identities = await _identities()
    assert [(i.user_id, i.subject) for i in identities] == [(bob, "subject-1")]
    first, second = await _tokens(bob)
    assert first.revoked_at is not None  # the older session ended on attach
    assert second.revoked_at is None and second.auth_method == f"sso:{TEST_TENANT_ID}"


async def test_password_login_without_the_link_cookie_never_attaches(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    await _bob(db, verified=False)
    connection = await seed_connection(db)
    with idp_installed(MockIdp(claims={"email": BOB})) as idp:
        await oidc_login(raw_client, idp, connection)
    raw_client.cookies.clear()
    assert (await _password_login(raw_client)).status_code == 200
    assert await _identities() == []


async def test_verified_account_is_attached_and_its_sessions_revoked(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    bob = await _bob(db, verified=True)
    assert (await _password_login(raw_client)).status_code == 200
    connection = await seed_connection(db)
    with idp_installed(MockIdp(claims={"email": BOB})) as idp:
        response = await oidc_login(raw_client, idp, connection)
    assert sso_error_of(response) is None
    old, new = await _tokens(bob)
    assert old.revoked_at is not None and old.auth_method == "password"
    assert new.revoked_at is None and new.auth_method == f"sso:{TEST_TENANT_ID}"


@pytest.mark.parametrize("trust", [False, True])
async def test_missing_email_verified_claim_attaches_only_with_trust_opt_in(
    raw_client: AsyncClient, db: AsyncSession, trust: bool
) -> None:
    await _bob(db, verified=True, password=False)
    connection = await seed_connection(db, trust_email_claim=trust)
    idp = MockIdp(claims={"email": BOB, "email_verified": None})
    with idp_installed(idp):
        response = await oidc_login(raw_client, idp, connection)
    assert sso_error_of(response) == (None if trust else "sso_email_unverified")
    assert len(await _identities()) == (1 if trust else 0)


async def test_password_signup_on_a_company_sso_domain_is_refused(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    body = {"email": f"new@{DOMAIN.upper()}", "password": "long enough pw", "display_name": "N"}
    await seed_connection(db, status="tested")
    assert (await raw_client.post("/api/auth/register", json=body)).status_code != 403
    raw_client.cookies.clear()
    await seed_connection(db)
    refused = await raw_client.post("/api/auth/register", json=body | {"email": f"n2@{DOMAIN}"})
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "sso_signup_blocked"


def test_emails_are_normalized_to_lower_local_at_idna_domain() -> None:
    assert normalize_email(" Bob@Bücher.Example ") == "bob@xn--bcher-kva.example"
    assert normalize_email("no-at-sign") is None
    assert normalize_email("a b@corp.example") is None
