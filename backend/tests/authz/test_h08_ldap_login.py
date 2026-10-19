"""H-08: LDAP/AD login against a fake directory (plan 10.10 "LDAP·AD")."""

from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sso import ldap
from app.auth.sso.ldap_login import MAX_ATTEMPTS, reset_throttle
from app.config import settings
from app.exceptions import AppError
from app.models.organization_identity import UserIdentity
from tests.authz.d04_support import seed_scope
from tests.authz.h08_flows import audit_actions
from tests.authz.h08_support import (
    DOMAIN,
    FakeDirectory,
    make_default_org,
    seed_connection,
    verified_domain,
)
from tests.conftest import TestSession


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession):
    await seed_scope(db)
    await make_default_org(db)
    await verified_domain(db)
    reset_throttle()


@pytest.fixture
def directory() -> Iterator[FakeDirectory]:
    fake = FakeDirectory()
    fake.add("alice", "correct", email=f"alice@{DOMAIN}", groups=("eng",))
    fake.add("mallory", "pw", email="mallory@elsewhere.example")
    previous = ldap.directory()
    ldap.set_directory(fake)
    yield fake
    ldap.set_directory(previous)


async def _login(client: AsyncClient, connection: object, username: str, password: str):
    discover = await client.post("/api/auth/sso/discover", json={"email": f"x@{DOMAIN}"})
    token = discover.json()["login_csrf"]
    return await client.post(
        f"/api/auth/ldap/{connection}/login",
        json={"username": username, "password": password},
        headers={"X-Login-CSRF": token},
    )


async def test_ldap_login_success_issues_the_jwt_cookies(
    raw_client: AsyncClient, db: AsyncSession, directory: FakeDirectory
) -> None:
    connection = await seed_connection(db, "ldap", jit_enabled=True)
    response = await _login(raw_client, connection, "alice", "correct")
    assert response.status_code == 200, response.text
    assert response.json()["user"]["email"] == f"alice@{DOMAIN}"
    assert settings.cookie_name_access in response.cookies
    async with TestSession() as fresh:
        identity = await fresh.scalar(select(UserIdentity))
        assert identity is not None
        assert (identity.issuer, identity.subject) == (f"ldap:{connection}", "uuid-alice")


async def test_wrong_password_and_unknown_user_are_indistinguishable(
    raw_client: AsyncClient, db: AsyncSession, directory: FakeDirectory
) -> None:
    connection = await seed_connection(db, "ldap", jit_enabled=True)
    wrong = await _login(raw_client, connection, "alice", "wrong")
    unknown = await _login(raw_client, connection, "nobody", "correct")
    unverified = await _login(raw_client, connection, "mallory", "pw")
    assert wrong.status_code == unknown.status_code == unverified.status_code == 401
    assert wrong.json() == unknown.json() == unverified.json()
    assert wrong.json()["error"]["code"] == "invalid_credentials"
    reasons = [str(e.reason_code) for e in await audit_actions("auth.sso_login")]
    assert sorted(reasons) == [
        "invalid_credentials",
        "invalid_credentials",
        "sso_domain_unverified",
    ]


async def test_empty_password_never_reaches_the_directory(
    raw_client: AsyncClient, db: AsyncSession, directory: FakeDirectory
) -> None:
    connection = await seed_connection(db, "ldap", jit_enabled=True)
    response = await _login(raw_client, connection, "alice", "")
    assert response.status_code == 401
    assert directory.searches == []


async def test_user_name_is_escaped_in_the_search_filter(
    raw_client: AsyncClient, db: AsyncSession, directory: FakeDirectory
) -> None:
    connection = await seed_connection(db, "ldap", jit_enabled=True)
    response = await _login(raw_client, connection, "*)(uid=*", "correct")
    assert response.status_code == 401
    assert directory.searches == [r"(uid=\2a\29\28uid=\2a)"]


async def test_login_csrf_token_is_required(
    raw_client: AsyncClient, db: AsyncSession, directory: FakeDirectory
) -> None:
    connection = await seed_connection(db, "ldap", jit_enabled=True)
    response = await raw_client.post(
        f"/api/auth/ldap/{connection}/login", json={"username": "alice", "password": "correct"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "login_csrf_invalid"
    assert directory.searches == []


async def test_same_user_name_is_rate_limited(
    raw_client: AsyncClient, db: AsyncSession, directory: FakeDirectory
) -> None:
    connection = await seed_connection(db, "ldap", jit_enabled=True)
    for _ in range(MAX_ATTEMPTS):
        assert (await _login(raw_client, connection, "Alice", "wrong")).status_code == 401
    limited = await _login(raw_client, connection, "alice", "correct")
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "login_rate_limited"


async def test_missing_ldap_client_is_503(raw_client: AsyncClient, db: AsyncSession) -> None:
    connection = await seed_connection(db, "ldap", jit_enabled=True)
    response = await _login(raw_client, connection, "alice", "correct")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ldap_unavailable"


@pytest.mark.parametrize(
    ("url", "start_tls", "allowed"),
    [
        ("ldaps://dir.corp.example", False, True),
        ("ldap://dir.corp.example", True, True),
        ("ldap://dir.corp.example", False, False),
        ("LDAP://dir.corp.example", False, False),
        ("dir.corp.example", True, False),
    ],
)
def test_plaintext_ldap_transport_is_refused(url: str, start_tls: bool, allowed: bool) -> None:
    if allowed:
        ldap.require_encrypted_transport(url, start_tls)
        return
    with pytest.raises(AppError) as caught:
        ldap.require_encrypted_transport(url, start_tls)
    assert caught.value.code == "ldap_plaintext_refused"
