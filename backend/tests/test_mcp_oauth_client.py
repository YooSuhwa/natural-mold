from __future__ import annotations

import httpx
import pytest

from app.credentials.mcp_oauth_client import (
    build_authorization_url,
    build_pkce_pair,
    discover_authorization_server_metadata,
    discover_protected_resource_metadata,
    dynamic_client_registration,
    exchange_authorization_code,
    select_grant_type_and_authentication,
    validate_oauth_url,
)


def test_validate_oauth_url_rejects_non_http_protocol() -> None:
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        validate_oauth_url("javascript:alert(1)")


def test_pkce_pair_uses_s256() -> None:
    pair = build_pkce_pair()
    assert pair.code_verifier
    assert pair.code_challenge
    assert pair.code_challenge_method == "S256"
    assert pair.code_verifier != pair.code_challenge


@pytest.mark.asyncio
async def test_discover_protected_resource_metadata_path_specific_first() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if str(request.url).endswith("/.well-known/oauth-protected-resource/v1/mcp/authv2"):
            return httpx.Response(200, json={"authorization_servers": ["https://id.atlassian.com"]})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await discover_protected_resource_metadata(
            "https://mcp.atlassian.com/v1/mcp/authv2",
            client=client,
        )

    assert data.authorization_servers == ["https://id.atlassian.com"]
    assert requested[0] == (
        "https://mcp.atlassian.com/.well-known/oauth-protected-resource/v1/mcp/authv2"
    )


@pytest.mark.asyncio
async def test_discover_authorization_server_metadata_supports_path_insertion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://issuer.example/.well-known/oauth-authorization-server/path":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://issuer.example/path",
                    "authorization_endpoint": "https://issuer.example/authorize",
                    "token_endpoint": "https://issuer.example/token",
                    "registration_endpoint": "https://issuer.example/register",
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "token_endpoint_auth_methods_supported": ["none"],
                    "code_challenge_methods_supported": ["S256"],
                    "scopes_supported": ["read:confluence", "read:jira"],
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await discover_authorization_server_metadata(
            "https://issuer.example/path",
            client=client,
        )

    assert data.authorization_endpoint == "https://issuer.example/authorize"
    assert data.token_endpoint == "https://issuer.example/token"
    assert data.registration_endpoint == "https://issuer.example/register"
    assert data.scopes_supported == ["read:confluence", "read:jira"]


@pytest.mark.asyncio
async def test_discover_authorization_server_metadata_falls_back_to_origin_registration() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if str(request.url) == "https://issuer.example/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "authorization_endpoint": "https://issuer.example/authorize",
                    "token_endpoint": "https://issuer.example/token",
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        if str(request.url) == "https://issuer.example/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "authorization_endpoint": "https://issuer.example/authorize",
                    "token_endpoint": "https://issuer.example/token",
                    "registration_endpoint": "https://issuer.example/register",
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await discover_authorization_server_metadata(
            "https://issuer.example/resource-id",
            client=client,
        )

    assert requested[-2:] == [
        "https://issuer.example/.well-known/oauth-authorization-server",
        "https://issuer.example/.well-known/openid-configuration",
    ]
    assert data.registration_endpoint == "https://issuer.example/register"


@pytest.mark.asyncio
async def test_discover_authorization_server_metadata_supports_suffix_well_known() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if (
            str(request.url)
            == "https://issuer.example/resource-id/.well-known/oauth-authorization-server"
        ):
            return httpx.Response(
                200,
                json={
                    "authorization_endpoint": "https://issuer.example/authorize",
                    "token_endpoint": "https://issuer.example/token",
                    "registration_endpoint": "https://issuer.example/resource-id/dcr/register",
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await discover_authorization_server_metadata(
            "https://issuer.example/resource-id",
            client=client,
        )

    assert data.registration_endpoint == "https://issuer.example/resource-id/dcr/register"


def test_select_grant_prefers_pkce_when_s256_available() -> None:
    selection = select_grant_type_and_authentication(
        grant_types=["authorization_code", "refresh_token"],
        token_endpoint_auth_methods=["none", "client_secret_basic"],
        code_challenge_methods=["S256"],
    )
    assert selection.grant_type == "pkce"
    assert selection.authentication == "none"


def test_select_grant_uses_pkce_with_confidential_client_when_s256_available() -> None:
    selection = select_grant_type_and_authentication(
        grant_types=["authorization_code", "refresh_token"],
        token_endpoint_auth_methods=["client_secret_basic"],
        code_challenge_methods=["S256"],
    )
    assert selection.grant_type == "pkce"
    assert selection.authentication == "header"


@pytest.mark.asyncio
async def test_dynamic_client_registration_posts_expected_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = request.read().decode()
        return httpx.Response(200, json={"client_id": "cid", "client_secret": "sec"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await dynamic_client_registration(
            registration_endpoint="https://issuer.example/register",
            redirect_uri="http://localhost:8001/api/oauth2-credential/callback",
            client_name="Moldy",
            scope="read:confluence read:jira",
            grant_type="pkce",
            authentication="none",
            client=client,
        )

    assert captured["url"] == "https://issuer.example/register"
    assert '"token_endpoint_auth_method":"none"' in str(captured["json"]).replace(" ", "")
    assert result.client_id == "cid"
    assert result.client_secret == "sec"


def test_build_authorization_url_includes_pkce_state_and_scope() -> None:
    url = build_authorization_url(
        authorization_endpoint="https://issuer.example/authorize",
        client_id="cid",
        redirect_uri="http://localhost:8001/api/oauth2-credential/callback",
        state="state-1",
        scope="read:jira",
        code_challenge="challenge",
    )
    assert "response_type=code" in url
    assert "client_id=cid" in url
    assert "state=state-1" in url
    assert "code_challenge=challenge" in url
    assert "code_challenge_method=S256" in url


@pytest.mark.asyncio
async def test_exchange_authorization_code_preserves_refresh_token_when_omitted() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "fresh",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        patch = await exchange_authorization_code(
            token_endpoint="https://issuer.example/token",
            code="code-1",
            redirect_uri="http://localhost:8001/api/oauth2-credential/callback",
            client_id="cid",
            client_secret=None,
            authentication="none",
            code_verifier="verifier",
            previous_refresh_token="old-refresh",
            client=client,
        )

    assert patch["access_token"] == "fresh"
    assert patch["refresh_token"] == "old-refresh"
    assert patch["token_type"] == "Bearer"
    assert patch["expires_at"] > 0
