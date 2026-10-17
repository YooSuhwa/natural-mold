"""F-15 test 2 (plan 11.4.1): RFC 9207 ``iss`` mismatch never reaches the token endpoint."""

from __future__ import annotations

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials.mcp_oauth_discovery import (
    AuthorizationServerIssuerError,
    discover_authorization_server_metadata,
)
from app.credentials.mcp_oauth_spec import issuer_matches
from app.models.credential_oauth_state import CredentialOAuthState
from tests.authz.f15_mock_as import ISSUER, MockAuthorizationServer, ensure_user, mcp_credential
from tests.authz.f15_mock_as import start as start_flow


def test_issuer_rules() -> None:
    assert issuer_matches(expected=ISSUER, received=ISSUER, required=True)
    assert not issuer_matches(expected=ISSUER, received=ISSUER + "/", required=True)
    assert not issuer_matches(expected=ISSUER, received=None, required=True)
    assert issuer_matches(expected=ISSUER, received=None, required=False)
    assert issuer_matches(expected=None, received="https://x.example", required=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("iss", ["https://evil.example", None])
async def test_issuer_mismatch_or_missing_blocks_code_exchange(
    db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, iss: str | None
) -> None:
    await ensure_user(db)
    server = MockAuthorizationServer(iss_supported=True)
    server.install(monkeypatch)
    cred = await mcp_credential(db)
    _, params = await start_flow(client, cred)

    row = (await db.execute(select(CredentialOAuthState))).scalar_one()
    assert (row.metadata_json or {})["issuer"] == ISSUER
    assert row.code_verifier

    query = {"code": "c1", "state": params["state"]}
    if iss is not None:
        query["iss"] = iss
    response = await client.get("/api/oauth2-credential/callback", params=query)

    assert response.status_code == 400
    assert "invalid authorization response" in response.text
    assert "evil" not in response.text and ISSUER not in response.text
    assert server.token_requests == []
    await db.refresh(row)
    assert row.consumed_at is not None  # one-shot

    replay = await client.get(
        "/api/oauth2-credential/callback",
        params={"code": "c1", "state": params["state"], "iss": ISSUER},
    )
    assert replay.status_code == 400
    assert server.token_requests == []


@pytest.mark.asyncio
async def test_matching_issuer_exchanges_the_code(
    db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await ensure_user(db)
    server = MockAuthorizationServer(iss_supported=True)
    server.install(monkeypatch)
    _, params = await start_flow(client, await mcp_credential(db))

    ok = await client.get(
        "/api/oauth2-credential/callback",
        params={"code": "c1", "state": params["state"], "iss": ISSUER},
    )
    assert ok.status_code == 200
    assert len(server.token_requests) == 1


def _as_metadata(issuer: str | None) -> dict[str, object]:
    body: dict[str, object] = {
        "authorization_endpoint": "https://as.example/authorize",
        "token_endpoint": "https://as.example/token",
        "code_challenge_methods_supported": ["S256"],
    }
    if issuer is not None:
        body["issuer"] = issuer
    return body


async def _discover(routes: dict[str, dict[str, object]], url: str):
    def handler(request: httpx.Request) -> httpx.Response:
        body = routes.get(str(request.url))
        return httpx.Response(200, json=body) if body else httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await discover_authorization_server_metadata(url, client=client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "routes"),
    [
        # issuer missing
        (ISSUER, {f"{ISSUER}/.well-known/oauth-authorization-server": _as_metadata(None)}),
        # issuer names another authorization server
        (
            ISSUER,
            {
                f"{ISSUER}/.well-known/oauth-authorization-server": _as_metadata(
                    "https://evil.example"
                )
            },
        ),
        # found only via the origin-root fallback, claiming the origin as issuer
        (
            f"{ISSUER}/tenant",
            {f"{ISSUER}/.well-known/oauth-authorization-server": _as_metadata(ISSUER)},
        ),
    ],
)
async def test_discovery_rejects_unestablished_issuer(
    url: str, routes: dict[str, dict[str, object]]
) -> None:
    with pytest.raises(AuthorizationServerIssuerError):
        await _discover(routes, url)


@pytest.mark.asyncio
async def test_discovery_accepts_issuer_equal_to_the_server_url_via_fallback() -> None:
    url = f"{ISSUER}/tenant"
    routes = {f"{ISSUER}/.well-known/oauth-authorization-server": _as_metadata(url)}
    assert (await _discover(routes, url)).issuer == url


@pytest.mark.asyncio
async def test_start_fails_closed_when_issuer_cannot_be_established(
    db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await ensure_user(db)
    MockAuthorizationServer(issuer="https://evil.example").install(monkeypatch)

    response, _ = await start_flow(client, await mcp_credential(db))

    assert response.status_code == 400
    assert "issuer could not be verified" in response.text
    assert (await db.execute(select(CredentialOAuthState))).scalars().all() == []


@pytest.mark.asyncio
async def test_manual_config_records_issuer_from_named_authorization_server(
    db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await ensure_user(db)
    MockAuthorizationServer().install(monkeypatch)
    cred = await mcp_credential(
        db,
        use_dynamic_client_registration=False,
        authorization_server_url=ISSUER,
        auth_url=f"{ISSUER}/authorize",
        access_token_url=f"{ISSUER}/token",
        client_id="manual",
    )

    response, _ = await start_flow(client, cred)

    assert response.status_code == 200
    row = (await db.execute(select(CredentialOAuthState))).scalar_one()
    assert (row.metadata_json or {})["issuer"] == ISSUER
