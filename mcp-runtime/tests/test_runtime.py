"""The runtime only answers calls that carry a valid run token for the path they call."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.conftest import make_token


async def _whoami(url: str, token: str) -> dict[str, Any]:
    transport = StreamableHttpTransport(
        f"{url}/mcp/system/diagnostics/", headers={"Authorization": f"Bearer {token}"}
    )
    async with Client(transport) as client:
        result = await client.call_tool("whoami", {})
        assert isinstance(result.structured_content, dict)
        return result.structured_content


async def test_health(runtime_url: str) -> None:
    async with httpx.AsyncClient() as http:
        response = await http.get(f"{runtime_url}/healthz")
    assert response.json() == {"status": "ok"}


async def test_a_valid_token_reaches_the_tool_with_its_claims(runtime_url: str) -> None:
    token = make_token(run_id="11111111-1111-1111-1111-111111111111")

    claims = await _whoami(runtime_url, token)

    assert claims["run_id"] == "11111111-1111-1111-1111-111111111111"
    assert claims["server"] == "system/diagnostics"


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not-a-jwt",
        make_token(_key="another-secret-" + "y" * 32),
        make_token(exp=int(time.time()) - 10),
        make_token(server="openapi/123"),  # made for another server
        make_token(aud="someone-else"),
        make_token(iss="someone-else"),
        make_token(exp=int(time.time()) + 3600),  # longer than five minutes
        make_token(run_id=None),
        make_token(tenant_id="not-a-uuid"),
    ],
)
async def test_anything_else_is_refused_before_the_mcp_server(runtime_url: str, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient() as http:
        response = await http.post(
            f"{runtime_url}/mcp/system/diagnostics/",
            headers={**headers, "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_the_runtime_will_not_start_without_its_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    from moldy_mcp_runtime.app import create_app

    monkeypatch.setenv("MCP_RUNTIME_TOKEN_SECRET", "short")
    with pytest.raises(RuntimeError, match="MCP_RUNTIME_TOKEN_SECRET"):
        create_app()
