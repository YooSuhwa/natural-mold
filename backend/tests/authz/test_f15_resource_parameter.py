"""F-15 test 1 (plan 11.4.1): RFC 8707 ``resource`` on authorization and token requests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.credentials.mcp_oauth_spec import canonical_resource_uri, token_destination_allowed
from app.credentials.registry import registry
from tests.authz.f15_mock_as import (
    ISSUER,
    RESOURCE,
    MockAuthorizationServer,
    ensure_user,
    mcp_credential,
    start,
)


def test_canonical_resource_uri_normalizes_scheme_host_fragment_and_slash() -> None:
    assert canonical_resource_uri("HTTPS://MCP.Example:8443/v1/MCP/?x=1#frag") == (
        "https://mcp.example:8443/v1/MCP"
    )
    assert canonical_resource_uri("https://mcp.example:443/") == "https://mcp.example"
    assert canonical_resource_uri("http://mcp.example:80/a") == "http://mcp.example/a"


def test_token_only_travels_to_its_own_server_origin_and_never_in_query() -> None:
    assert token_destination_allowed(resource=RESOURCE, request_url="https://mcp.example/mcp")
    assert token_destination_allowed(resource=RESOURCE, request_url="https://mcp.example:443/x")
    assert not token_destination_allowed(resource=RESOURCE, request_url="https://other.example/")
    assert not token_destination_allowed(
        resource=RESOURCE, request_url="https://mcp.example/mcp?access_token=x"
    )


@pytest.mark.asyncio
async def test_resource_is_sent_on_authorization_code_exchange_and_refresh(
    db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await ensure_user(db)
    server = MockAuthorizationServer()
    server.install(monkeypatch)
    cred = await mcp_credential(db)

    response, params = await start(client, cred)
    assert response.status_code == 200
    assert params["resource"] == RESOURCE
    assert params["code_challenge_method"] == "S256"

    callback = await client.get(
        "/api/oauth2-credential/callback",
        params={"code": "c1", "state": params["state"], "iss": ISSUER},
    )
    assert callback.status_code == 200
    assert server.token_requests[-1]["grant_type"] == "authorization_code"
    assert server.token_requests[-1]["resource"] == RESOURCE
    assert server.token_requests[-1]["code_verifier"]

    await db.refresh(cred)
    data = await credential_service.decrypt_with_external(cred)
    await registry.require("mcp_oauth2").pre_authentication(data)  # type: ignore[misc]
    assert server.token_requests[-1]["grant_type"] == "refresh_token"
    assert server.token_requests[-1]["resource"] == RESOURCE
