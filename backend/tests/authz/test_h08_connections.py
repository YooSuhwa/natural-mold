"""H-08: S30 connection configuration, test login and activation (plan 9.7, 10.10)."""

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sso import ldap
from app.auth.sso.ldap_login import reset_throttle
from app.config import settings
from app.models.organization_identity import SsoConnection
from tests.authz.d04_support import OTHER_ORG_ID, seed_scope
from tests.authz.d05_support import TEST_ORG_ID
from tests.authz.e09_support import MEMBER, TENANT, acting_as, make_admin
from tests.authz.h08_flows import idp_installed, sso_error_of
from tests.authz.h08_support import (
    BIND_PASSWORD,
    CLIENT_ID,
    CLIENT_SECRET,
    DOMAIN,
    ISSUER,
    FakeDirectory,
    MockIdp,
)
from tests.conftest import TestSession

CONNECTIONS = f"{TENANT}/sso-connections"
OIDC = {
    "protocol": "oidc",
    "issuer": ISSUER,
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
}
LDAP = {
    "protocol": "ldap",
    "server_url": "ldap://dir.corp.example",
    "start_tls": True,
    "bind_dn": "cn=svc,dc=corp",
    "bind_password": BIND_PASSWORD,
    "user_base_dn": "dc=corp",
    "user_filter": "(uid={username})",
}


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession, test_app: FastAPI):
    await seed_scope(db)
    reset_throttle()
    with acting_as(test_app, MEMBER, TEST_ORG_ID):
        yield


@pytest.fixture
def directory() -> Iterator[FakeDirectory]:
    fake = FakeDirectory()
    fake.add("alice", "correct", email=f"alice@{DOMAIN}")
    previous = ldap.directory()
    ldap.set_directory(fake)
    yield fake
    ldap.set_directory(previous)


async def test_member_cannot_manage_connections(client: AsyncClient) -> None:
    assert (await client.get(CONNECTIONS)).status_code == 403
    assert (await client.post(CONNECTIONS, json=OIDC)).status_code == 403


async def test_oidc_connection_secret_is_sealed_and_never_echoed(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    created = await client.post(CONNECTIONS, json=OIDC | {"scopes": ["email", "groups"]})
    assert created.status_code == 201, created.text
    body = created.json()
    assert CLIENT_SECRET not in created.text
    assert body["status"] == "draft" and body["has_secret"] is True
    assert body["scopes"] == ["openid", "email", "groups"]
    assert body["redirect_uri"].endswith(f"/auth/callback/{body['id']}")
    async with TestSession() as fresh:
        row = await fresh.get(SsoConnection, UUID(body["id"]))
        assert row is not None and row.client_secret_encrypted
        assert CLIENT_SECRET not in row.client_secret_encrypted
    activate = await client.post(f"{CONNECTIONS}/{body['id']}/activate")
    assert activate.status_code == 409
    assert activate.json()["error"]["code"] == "sso_connection_not_tested"


@pytest.mark.parametrize(
    "change",
    [
        {"server_url": "ldap://dir.corp.example", "start_tls": False},
        {"server_url": "dir.corp.example"},
    ],
)
async def test_plaintext_ldap_is_refused_on_save(
    client: AsyncClient, db: AsyncSession, change: dict[str, object]
) -> None:
    await make_admin(db)
    response = await client.post(CONNECTIONS, json=LDAP | change)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ldap_plaintext_refused"


async def test_ldap_test_then_activate_then_retest_after_change(
    client: AsyncClient, db: AsyncSession, directory: FakeDirectory
) -> None:
    await make_admin(db)
    body = (await client.post(CONNECTIONS, json=LDAP)).json()
    url = f"{CONNECTIONS}/{body['id']}"
    failed = await client.post(f"{url}/test", json={"username": "alice", "password": "bad"})
    assert failed.json()["result"] == {"ok": False, "error": "invalid_credentials", "claims": None}
    assert failed.json()["connection"]["status"] == "draft"
    passed = await client.post(f"{url}/test", json={"username": "alice", "password": "correct"})
    assert passed.status_code == 200, passed.text
    assert passed.json()["result"]["ok"] is True
    assert passed.json()["result"]["claims"]["email"] == f"alice@{DOMAIN}"
    assert BIND_PASSWORD not in passed.text and "correct" not in passed.text
    assert passed.json()["connection"]["status"] == "tested"
    activated = await client.post(f"{url}/activate")
    assert activated.json()["status"] == "active"
    harmless = await client.patch(url, json={"jit_enabled": True})
    assert harmless.json()["status"] == "active"
    changed = await client.patch(url, json={"bind_dn": "cn=other,dc=corp"})
    assert changed.json()["status"] == "draft"
    plaintext = await client.patch(url, json={"start_tls": False})
    assert plaintext.status_code == 422
    foreign = await client.patch(url, json={"client_id": "x"})
    assert foreign.status_code == 422


async def test_group_mapping_must_target_this_company(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    good = {"eng": {"org_id": str(OTHER_ORG_ID), "role": "builder"}}
    created = await client.post(CONNECTIONS, json=OIDC | {"group_role_mapping": good})
    assert created.status_code == 201, created.text
    bad = {"eng": {"org_id": "00000000-0000-0000-0000-00000000dead", "role": "builder"}}
    refused = await client.post(CONNECTIONS, json=OIDC | {"group_role_mapping": bad})
    assert refused.status_code == 422
    wrong_role = {"eng": {"org_id": str(OTHER_ORG_ID), "role": "owner"}}
    assert (
        await client.post(CONNECTIONS, json=OIDC | {"group_role_mapping": wrong_role})
    ).status_code == 422


async def test_oidc_test_login_marks_tested_without_signing_in(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    body = (await client.post(CONNECTIONS, json=OIDC)).json()
    with idp_installed(MockIdp()) as idp:
        started = await client.post(f"{CONNECTIONS}/{body['id']}/test")
        assert started.status_code == 200, started.text
        code, state = idp.authorize(started.json()["authorize_url"])
        callback = await client.get(
            f"/auth/callback/{body['id']}", params={"code": code, "state": state}
        )
    assert callback.status_code == 302
    assert callback.headers["location"].endswith(
        f"/settings/company/sso?test={body['id']}&result=ok"
    )
    assert sso_error_of(callback) is None
    assert settings.cookie_name_access not in callback.cookies
    current = (await client.get(f"{CONNECTIONS}/{body['id']}")).json()
    assert current["status"] == "tested" and current["tested_at"]


async def test_failed_oidc_test_is_reported_and_keeps_draft(
    client: AsyncClient, db: AsyncSession
) -> None:
    await make_admin(db)
    body = (await client.post(CONNECTIONS, json=OIDC)).json()
    with idp_installed(MockIdp(discovery_issuer="https://evil.example.test")):
        started = await client.post(f"{CONNECTIONS}/{body['id']}/test")
    assert started.json()["authorize_url"] is None
    assert started.json()["result"]["error"] == "sso_issuer_mismatch"
    assert started.json()["connection"]["status"] == "draft"
