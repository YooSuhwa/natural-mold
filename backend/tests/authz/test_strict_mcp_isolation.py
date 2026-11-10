"""Strict MCP loading: per-server auth, single send, opaque ids, typed failures."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.tools import BaseTool, StructuredTool

from app.agent_runtime.mcp_preparation import McpPreparationError, prepare_mcp_tools
from app.tools.keys import ref_of

PATH_SECRET = "sk-path-secret-123"


def _config(name: str, url: str, token: str) -> dict[str, Any]:
    return {
        "name": name,
        "mcp_tool_name": name,
        "mcp_server_url": url,
        "mcp_transport_headers": {"X-Tenant": url},
        "auth_config": {"api_token": token},
    }


def _tool(name: str, calls: list[str], *, fail: bool = False) -> BaseTool:
    async def call(**_kwargs: Any) -> str:
        calls.append(name)
        if fail:
            raise ConnectionError("transport dropped")
        return name

    return StructuredTool.from_function(coroutine=call, name=name, description=name)


@pytest.mark.asyncio
async def test_auth_injection_is_scoped_per_server_and_names_are_opaque() -> None:
    injected: dict[str, dict[str, Any]] = {}
    calls: list[str] = []

    async def discover(key: str, config: dict, inject: dict) -> list[BaseTool]:
        injected[config["url"]] = inject
        return [_tool("search", calls)]

    configs = [
        {**_config("search", "https://a.test/mcp", "token-a"), "mcp_tool_id": "tool-a"},
        {
            **_config("search", f"https://b.test/mcp/{PATH_SECRET}", "token-b"),
            "mcp_tool_id": "tool-b",
        },
    ]
    prepared = await prepare_mcp_tools(
        configs, per_server_timeout=1.0, total_timeout=2.0, discover=discover
    )

    # Each server is handed only its own argument auth (app.mcp.sdk merges it per call).
    assert injected == {
        config["mcp_server_url"]: {"search": {"api_token": config["auth_config"]["api_token"]}}
        for config in configs
    }
    names = sorted(item.tool_name for item in prepared.tools)
    assert len(set(names)) == 2 and all(n.startswith("srv_") for n in names)
    # The rename keeps each tool's resource key and the name its settings were saved under.
    refs = sorted((ref_of(item.tool).key, ref_of(item.tool).name) for item in prepared.tools)
    assert refs == [("mcp:tool-a", "search"), ("mcp:tool-b", "search")]
    assert {item.mcp_tool_name for item in prepared.tools} == {"search"}
    assert PATH_SECRET not in repr([item.digest_fields() for item in prepared.tools])
    assert len({item.header_digest for item in prepared.tools}) == 2


@pytest.mark.asyncio
async def test_failed_call_is_sent_exactly_once() -> None:
    calls: list[str] = []

    async def discover(_key: str, _config: dict, _inject: dict) -> list[BaseTool]:
        return [_tool("write", calls, fail=True)]

    prepared = await prepare_mcp_tools(
        [_config("write", "https://a.test/mcp", "t")],
        per_server_timeout=1.0,
        total_timeout=2.0,
        discover=discover,
    )
    result = await prepared.tools[0].tool.ainvoke({})

    assert calls == ["write"]
    assert "[MCP Tool Error]" in str(result)


@pytest.mark.asyncio
async def test_url_secret_never_reaches_failure_reason() -> None:
    async def discover(_key: str, _config: dict, _inject: dict) -> list[BaseTool]:
        raise ConnectionError(f"refused {PATH_SECRET}")

    with pytest.raises(McpPreparationError) as error:
        await prepare_mcp_tools(
            [_config("search", f"https://b.test/mcp/{PATH_SECRET}", "t")],
            per_server_timeout=1.0,
            total_timeout=2.0,
            discover=discover,
        )
    assert PATH_SECRET not in str(error.value)
    assert error.value.reason.startswith("discovery:srv_")


@pytest.mark.asyncio
async def test_malformed_config_is_a_typed_preparation_failure() -> None:
    with pytest.raises(McpPreparationError) as error:
        await prepare_mcp_tools([{"name": "search"}], per_server_timeout=1.0, total_timeout=2.0)
    assert error.value.code == "mcp_preparation_failed"
    assert error.value.reason == "internal:KeyError"
