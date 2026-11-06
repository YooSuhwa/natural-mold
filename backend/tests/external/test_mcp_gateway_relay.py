"""The backend's MCP calls through a real gateway relay to a real MCP server (D10).

Skipped unless ``MCP_RUNTIME_TEST_URL`` and ``MCP_RUNTIME_TEST_SECRET`` are set to a running
runtime (CI's external-services job). The upstream is a local FastMCP server.
"""

from __future__ import annotations

import os

import httpx
import pytest

from app.agent_runtime.mcp_auth_errors import classify_mcp_error
from app.config import settings
from app.mcp import sdk
from app.mcp.client import connect_and_list
from app.mcp.invocation import call_mcp_tool_once, read_mcp_resource_once
from tests.mcp_http_fixture import refusing, serve, weather_server

pytestmark = pytest.mark.skipif(
    not os.environ.get("MCP_RUNTIME_TEST_URL"), reason="needs a runtime"
)


@pytest.fixture(autouse=True)
def _gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mcp_gateway_enabled", True)
    monkeypatch.setattr(settings, "mcp_runtime_url", os.environ["MCP_RUNTIME_TEST_URL"])
    monkeypatch.setattr(settings, "mcp_runtime_token_secret", os.environ["MCP_RUNTIME_TEST_SECRET"])


async def test_discovery_calls_and_resources_go_through_the_relay() -> None:
    with serve(weather_server()) as (url, log):
        probe = await connect_and_list(
            transport="streamable_http", url=url, headers={"X-Tenant": "one"}
        )
        called = await call_mcp_tool_once(
            transport="streamable_http",
            url=url,
            headers={"X-Tenant": "one"},
            tool_name="search_confluence",
            arguments={"query": "Moldy"},
        )
        resource = await read_mcp_resource_once(
            transport="streamable_http",
            url=url,
            headers=None,
            resource_uri="ui://weather/dashboard",
        )

    assert probe["success"] is True, probe["error"]
    assert [t["name"] for t in probe["tools"]] == ["search_confluence"]
    assert called["success"] is True, called
    assert called["structured_content"] == {"query": "Moldy"}
    assert resource["success"] is True and resource["contents"][0]["text"].startswith("<!doctype")
    # The server saw the sealed headers, never the relay token.
    assert {h.get("x-tenant") for h in log.headers if "x-tenant" in h} == {"one"}
    assert all(not h.get("authorization") for h in log.headers)


async def test_a_tool_the_connection_may_not_call_is_refused_by_the_relay() -> None:
    with serve(weather_server()) as (url, _log):
        transport = sdk.http_transport(
            "streamable_http", url, scope=sdk.GatewayScope(tools=("other_tool",))
        )
        with pytest.raises(Exception, match="not allowed for this run"):
            await sdk.call_tool(transport, "search_confluence", {"query": "x"})


async def test_an_upstream_401_still_reads_as_an_auth_error() -> None:
    with (
        refusing(401, 'Bearer error="invalid_token"') as url,
        pytest.raises(httpx.HTTPStatusError) as caught,
    ):
        await sdk.discover(sdk.http_transport("streamable_http", url))

    error = classify_mcp_error(caught.value)
    assert error is not None and (error.status_code, error.reason) == (401, "expired")
