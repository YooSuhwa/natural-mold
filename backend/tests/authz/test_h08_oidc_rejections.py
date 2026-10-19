"""H-08: the OIDC client fails closed — issuer, signature, nonce, state, PKCE, status."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
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
from tests.conftest import TestSession


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession):
    await seed_scope(db)
    await make_default_org(db)
    await verified_domain(db)


async def _no_user() -> bool:
    async with TestSession() as db:
        return await db.scalar(select(User).where(User.email == f"alice@{DOMAIN}")) is None


async def test_discovery_issuer_mismatch_never_redirects_to_the_idp(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    connection = await seed_connection(db, jit_enabled=True)
    with idp_installed(MockIdp(discovery_issuer="https://evil.example.test")):
        start = await raw_client.get(f"/api/auth/sso/{connection}/start")
    assert start.status_code == 302
    assert sso_error_of(start) == "sso_issuer_mismatch"


@pytest.mark.parametrize(
    ("idp", "code"),
    [
        (MockIdp(token_overrides={"iss": "https://evil.example.test"}), "sso_issuer_mismatch"),
        (MockIdp(token_overrides={"aud": "someone-else"}), "sso_token_invalid"),
        (MockIdp(token_overrides={"nonce": "replayed"}), "sso_token_invalid"),
        (MockIdp(token_overrides={"exp": 1}), "sso_token_invalid"),
        (MockIdp(sign_with_other_key=True), "sso_token_invalid"),
    ],
)
async def test_id_token_checks_fail_closed(
    raw_client: AsyncClient, db: AsyncSession, idp: MockIdp, code: str
) -> None:
    connection = await seed_connection(db, jit_enabled=True)
    with idp_installed(idp):
        response = await oidc_login(raw_client, idp, connection)
    assert sso_error_of(response) == code
    assert settings.cookie_name_access not in response.cookies
    assert await _no_user()


async def test_state_mismatch_is_rejected_before_any_token_call(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    connection = await seed_connection(db, jit_enabled=True)
    with idp_installed(MockIdp()) as idp:
        response = await oidc_login(raw_client, idp, connection, state="forged")
    assert sso_error_of(response) == "sso_state_invalid"
    assert idp.token_requests == []


async def test_callback_without_the_browser_cookie_is_rejected(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    connection = await seed_connection(db, jit_enabled=True)
    with idp_installed(MockIdp()) as idp:
        start = await raw_client.get(f"/api/auth/sso/{connection}/start")
        code, state = idp.authorize(start.headers["location"])
        raw_client.cookies.clear()
        response = await raw_client.get(
            f"/auth/callback/{connection}", params={"code": code, "state": state}
        )
    assert sso_error_of(response) == "sso_state_invalid"


async def test_idp_error_and_inactive_connection(raw_client: AsyncClient, db: AsyncSession) -> None:
    connection = await seed_connection(db, jit_enabled=True)
    draft = await seed_connection(db, status="tested")
    with idp_installed(MockIdp()) as idp:
        start = await raw_client.get(f"/api/auth/sso/{connection}/start")
        _, state = idp.authorize(start.headers["location"])
        denied = await raw_client.get(
            f"/auth/callback/{connection}", params={"error": "access_denied", "state": state}
        )
        inactive = await raw_client.get(f"/api/auth/sso/{draft}/start")
    assert sso_error_of(denied) == "sso_idp_error"
    assert sso_error_of(inactive) == "sso_unavailable"


async def test_plain_http_issuer_is_refused_by_the_outbound_client(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    connection = await seed_connection(db, issuer="http://idp.example.test", jit_enabled=True)
    with idp_installed(MockIdp(issuer="http://idp.example.test")):
        start = await raw_client.get(f"/api/auth/sso/{connection}/start")
    assert sso_error_of(start) == "sso_discovery_failed"
