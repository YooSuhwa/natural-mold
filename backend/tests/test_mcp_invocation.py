from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.mcp.invocation import call_mcp_tool_once


@pytest.mark.asyncio
async def test_call_mcp_tool_once_returns_content() -> None:
    captured: dict[str, Any] = {}

    class _FakeResult:
        content = [{"type": "text", "text": "Confluence page result"}]
        structuredContent = None

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
