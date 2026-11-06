from __future__ import annotations

import pytest
from pydantic import AnyUrl

from app.mcp.invocation import call_mcp_tool_once, read_mcp_resource_once
from tests.mcp_http_fixture import serve, weather_server


@pytest.mark.asyncio
async def test_call_mcp_tool_once_returns_content() -> None:
    with serve(weather_server()) as (url, log):
        result = await call_mcp_tool_once(
            transport="streamable_http",
            url=url,
            headers={"Authorization": "Bearer token"},
            tool_name="search_confluence",
            arguments={"query": "Moldy"},
        )

    assert result["success"] is True, result
    assert result["content"] == [{"type": "text", "text": "Confluence page result for Moldy"}]
    assert result["structured_content"] == {"query": "Moldy"}
    assert result["meta"]["viewUUID"] == "view-1"
    assert {h.get("authorization") for h in log.headers} == {"Bearer token"}


@pytest.mark.asyncio
async def test_read_mcp_resource_once_returns_ui_resource_metadata() -> None:
    with serve(weather_server()) as (url, _log):
        result = await read_mcp_resource_once(
            transport="streamable_http",
            url=url,
            headers={"Authorization": "Bearer token"},
            resource_uri="ui://weather/dashboard",
        )

    assert result["success"] is True, result
    [content] = result["contents"]
    assert content["uri"] == "ui://weather/dashboard"
    assert content["mimeType"] == "text/html;profile=mcp-app"
    assert content["_meta"]["ui"]["prefersBorder"] is True


def test_jsonable_serializes_pydantic_resource_uri_as_string() -> None:
    from app.mcp.invocation import _jsonable

    assert _jsonable(AnyUrl("ui://weather/dashboard")) == "ui://weather/dashboard"


@pytest.mark.asyncio
async def test_read_mcp_resource_once_explicitly_rejects_stdio_transport() -> None:
    result = await read_mcp_resource_once(
        transport="stdio",
        url=None,
        headers=None,
        resource_uri="ui://weather/dashboard",
    )

    assert result == {
        "success": False,
        "error": "resource reads are not supported for transport 'stdio'",
    }


@pytest.mark.asyncio
async def test_read_mcp_resource_once_uses_sse_transport_client() -> None:
    with serve(weather_server(), "sse") as (url, log):
        result = await read_mcp_resource_once(
            transport="sse",
            url=url,
            headers={"X-Tenant": "one"},
            resource_uri="ui://weather/dashboard",
        )

    assert result["success"] is True, result
    assert result["contents"][0]["text"] == "<!doctype html><title>Weather</title>"
    assert log.headers and {h.get("x-tenant") for h in log.headers} == {"one"}
