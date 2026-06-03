"""Runtime MCP tool cache tests."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.tools import StructuredTool


def _tool(name: str):
    async def _call(**kwargs: Any) -> str:
        return "ok"

    return StructuredTool.from_function(coroutine=_call, name=name, description=name)


@pytest.mark.asyncio
async def test_build_mcp_tools_reuses_cached_server_tools(monkeypatch) -> None:
    from app.agent_runtime import mcp_cache
    from app.agent_runtime.executor import _build_mcp_tools

    await mcp_cache.clear_mcp_tool_cache()
    calls: list[dict[str, Any]] = []

    class FakeMCPClient:
        def __init__(self, servers: dict[str, Any], **kwargs: Any) -> None:
            calls.append({"servers": servers, "kwargs": kwargs})

        async def get_tools(self):
            return [_tool("echo")]

    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient",
        FakeMCPClient,
    )

    configs = [
        {
            "definition_key": "mcp",
            "name": "echo",
            "mcp_server_url": "https://mcp.example.com",
            "mcp_tool_name": "echo",
            "mcp_transport_headers": {"Authorization": "Bearer abc"},
        }
    ]

    first = await _build_mcp_tools(configs)
    second = await _build_mcp_tools(configs)

    assert [tool.name for tool in first] == ["echo"]
    assert [tool.name for tool in second] == ["echo"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_mcp_tool_with_retry_returns_error_string_on_final_failure() -> None:
    from app.agent_runtime.mcp_cache import MCPToolWithRetry

    attempts = 0

    async def _call(**kwargs: Any) -> str:
        nonlocal attempts
        attempts += 1
        raise TimeoutError("slow")

    wrapped = MCPToolWithRetry(
        _tool("unstable"),
        max_retries=2,
        retry_delay=0,
        timeout_seconds=0.01,
    )
    object.__setattr__(wrapped, "_original_tool", _tool("unstable"))
    wrapped._original_tool.coroutine = _call  # type: ignore[method-assign]

    result = await wrapped.ainvoke({})

    assert attempts == 2
    assert "[MCP Tool Error]" in result
    assert "unstable" in result
