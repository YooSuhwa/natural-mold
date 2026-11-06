"""The backend's side of the MCP gateway (D10, app/mcp/gateway.py, app/mcp/sdk.py).

With the gateway on, nothing connects to an external MCP server: every transport points at
the runtime's relay and the upstream URL and headers travel sealed in its relay tokens.
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import settings
from app.credentials import mcp_oauth_discovery
from app.mcp import gateway, sdk
from app.outbound.destination import OutboundBlockedError

SECRET = "gateway-test-secret-" + "y" * 32
RUNTIME = "http://mcp-runtime:8096"


@pytest.fixture(autouse=True)
def _gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mcp_gateway_enabled", True)
    monkeypatch.setattr(settings, "mcp_runtime_url", RUNTIME)
    monkeypatch.setattr(settings, "mcp_runtime_token_secret", SECRET)
    monkeypatch.setattr(settings, "credential_http_allowed_hosts", "")


def _open(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The runtime's view of a relay token: its claims and the opened upstream."""
    claims = jwt.decode(token, SECRET, algorithms=["HS256"], audience="moldy-mcp-runtime")
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"moldy-mcp-relay-v1").derive(
        SECRET.encode()
    )
    blob = claims["upstream"]
    raw = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))
    opened = AESGCM(key).decrypt(raw[:12], raw[12:], claims["jti"].encode())
    return claims, json.loads(opened)


def _route(**overrides: Any) -> gateway.Relay:
    options: dict[str, Any] = {
        "transport": "streamable_http",
        "url": "https://mail.example.com/mcp?team=7",
        "headers": {"Authorization": "Bearer user-token"},
        "credentialed": True,
        "scope": gateway.GatewayScope(tools=("send",), actor_id=uuid.uuid4()),
    }
    options.update(overrides)
    url = options.pop("url")
    transport = options.pop("transport")
    headers = options.pop("headers")
    return gateway.relay(transport, url, headers, **options)


def test_the_relay_url_keeps_the_path_and_the_token_seals_the_upstream() -> None:
    route = _route()
    claims, upstream = _open(route.token())

    assert route.url == f"{RUNTIME}/relay/mcp?team=7"
    assert claims["server"] == "relay"
    assert claims["exp"] - claims["iat"] <= gateway.TOKEN_SECONDS
    assert "user-token" not in json.dumps(claims)  # sealed, not just signed
    assert upstream == {
        "url": "https://mail.example.com/mcp?team=7",
        "transport": "streamable_http",
        "headers": {"Authorization": "Bearer user-token"},
        "tools": ["send"],
        "resources": False,
        "https_only": True,
    }


def test_every_request_gets_its_own_token() -> None:
    route = _route()
    first, second = _open(route.token())[0], _open(route.token())[0]
    assert first["jti"] != second["jti"]


def test_a_credential_never_leaves_over_plain_http() -> None:
    with pytest.raises(OutboundBlockedError, match="https"):
        _route(url="http://mail.example.com/mcp")
    plain = _route(url="http://mail.example.com/mcp", credentialed=False)
    assert _open(plain.token())[1]["https_only"] is False


def test_the_gateway_needs_the_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mcp_runtime_url", "")
    with pytest.raises(gateway.GatewayUnavailable):
        sdk.http_transport("streamable_http", "https://mail.example.com/mcp")


@pytest.mark.parametrize("transport", ["streamable_http", "sse"])
def test_no_transport_points_at_the_upstream(transport: str) -> None:
    built = sdk.http_transport(
        transport,
        "https://mail.example.com/mcp",
        {"Authorization": "Bearer user-token"},
        credentialed=True,
    )
    assert built.url.startswith(f"{RUNTIME}/relay/")  # type: ignore[attr-defined]
    assert "mail.example.com" not in built.url  # type: ignore[attr-defined]
    assert built.headers == {}  # type: ignore[attr-defined]


def test_off_means_a_direct_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mcp_gateway_enabled", False)
    built = sdk.http_transport("streamable_http", "https://mail.example.com/mcp")
    assert built.url == "https://mail.example.com/mcp"  # type: ignore[attr-defined]


def test_an_agent_connection_may_call_only_the_tools_the_run_selected() -> None:
    from app.agent_runtime.mcp_tool_loader import _plan_mcp_servers, server_transport

    user = str(uuid.uuid4())
    configs = [
        {
            "name": name,
            "mcp_tool_name": name,
            "mcp_server_url": "https://mail.example.com/mcp",
            "user_id": user,
        }
        for name in ("send", "search")
    ]
    [config] = _plan_mcp_servers(configs).servers.values()
    built = server_transport(config)
    relay_auth = built.auth  # type: ignore[attr-defined]
    claims, upstream = _open(relay_auth._relay.token())

    assert upstream["tools"] == ["search", "send"]
    assert claims["sub"] == user


async def test_oauth_metadata_of_an_mcp_server_is_read_through_the_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []

    async def through_gateway(url: str) -> Any:
        asked.append(url)
        return {"authorization_servers": ["https://idp.example.com"]}

    def direct(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the MCP server was contacted directly")

    monkeypatch.setattr(gateway, "get_json", through_gateway)
    monkeypatch.setattr(mcp_oauth_discovery, "get_json", direct)

    found = await mcp_oauth_discovery.discover_protected_resource_metadata(
        "https://mail.example.com/mcp"
    )

    assert found.authorization_servers == ["https://idp.example.com"]
    assert asked == ["https://mail.example.com/.well-known/oauth-protected-resource/mcp"]
