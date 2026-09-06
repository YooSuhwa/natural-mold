from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from pydantic import AnyUrl

from app.mcp.invocation import call_mcp_tool_once, read_mcp_resource_once


@pytest.mark.asyncio
async def test_call_mcp_tool_once_returns_content() -> None:
    captured: dict[str, Any] = {}

    class _FakeResult:
        content = [{"type": "text", "text": "Confluence page result"}]
        structuredContent = None
        meta = {"viewUUID": "view-1"}

    class _FakeSession:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> None:
            return None

        async def initialize(self):
            return object()

        async def call_tool(self, name, arguments=None):
            captured["name"] = name
            captured["arguments"] = arguments
            return _FakeResult()

    def _fake_streamable(url, headers=None):
        captured["url"] = url
        captured["headers"] = headers

        class _Conn:
            async def __aenter__(self):
                return (None, None, None)

            async def __aexit__(self, *_exc):
                return None

        return _Conn()

    with (
        patch("mcp.client.session.ClientSession", _FakeSession),
        patch("mcp.client.streamable_http.streamablehttp_client", _fake_streamable),
    ):
        result = await call_mcp_tool_once(
            transport="streamable_http",
            url="https://mcp.example.com",
            headers={"Authorization": "Bearer token"},
            tool_name="search_confluence",
            arguments={"query": "Moldy"},
        )

    assert result["success"] is True
    assert captured["name"] == "search_confluence"
    assert captured["arguments"] == {"query": "Moldy"}
    assert result["content"][0]["text"] == "Confluence page result"
    assert result["meta"] == {"viewUUID": "view-1"}


@pytest.mark.asyncio
async def test_read_mcp_resource_once_returns_ui_resource_metadata() -> None:
    captured: dict[str, Any] = {}

    class _FakeContent:
        def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "uri": "ui://weather/dashboard",
                "mimeType": "text/html;profile=mcp-app",
                "text": "<!doctype html><title>Weather</title>",
                "_meta": {
                    "ui": {
                        "csp": {"connectDomains": ["https://api.weather.test"]},
                        "prefersBorder": True,
                    }
                },
            }

    class _FakeResult:
        contents = [_FakeContent()]
        meta = {"ignored": "result-level"}

    class _FakeSession:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def initialize(self):
            return object()

        async def read_resource(self, uri: str):
            captured["uri"] = uri
            return _FakeResult()

    def _fake_streamable(url: str, headers: dict[str, str] | None = None):
        captured["url"] = url
        captured["headers"] = headers

        class _Conn:
            async def __aenter__(self):
                return (None, None, None)

            async def __aexit__(self, *_exc: Any) -> None:
                return None

        return _Conn()

    with (
        patch("mcp.client.session.ClientSession", _FakeSession),
        patch("mcp.client.streamable_http.streamablehttp_client", _fake_streamable),
    ):
        result = await read_mcp_resource_once(
            transport="streamable_http",
            url="https://mcp.example.com",
            headers={"Authorization": "Bearer token"},
            resource_uri="ui://weather/dashboard",
        )

    assert result["success"] is True
    assert captured["uri"] == "ui://weather/dashboard"
    assert result["contents"][0]["_meta"]["ui"]["prefersBorder"] is True


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
    captured: dict[str, Any] = {}

    class _FakeResult:
        contents: list[Any] = []

    class _FakeSession:
        def __init__(self, *_args: Any) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def initialize(self) -> object:
            return object()

        async def read_resource(self, uri: str) -> _FakeResult:
            captured["uri"] = uri
            return _FakeResult()

    def _fake_sse(url: str, headers: dict[str, str] | None = None):
        captured.update(url=url, headers=headers)

        class _Conn:
            async def __aenter__(self):
                return None, None

            async def __aexit__(self, *_exc: Any) -> None:
                return None

        return _Conn()

    with (
        patch("mcp.client.session.ClientSession", _FakeSession),
        patch("mcp.client.sse.sse_client", _fake_sse),
    ):
        result = await read_mcp_resource_once(
            transport="sse",
            url="https://mcp.example.test/sse",
            headers={"X-Tenant": "one"},
            resource_uri="ui://weather/dashboard",
        )

    assert result["success"] is True
    assert captured == {
        "url": "https://mcp.example.test/sse",
        "headers": {"X-Tenant": "one"},
        "uri": "ui://weather/dashboard",
    }
