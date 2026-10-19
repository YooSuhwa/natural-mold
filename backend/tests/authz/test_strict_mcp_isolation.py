"""Strict MCP loading: per-server auth, single send, opaque ids, typed failures."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pytest
from langchain_core.tools import BaseTool, StructuredTool

from app.agent_runtime.mcp_preparation import McpPreparationError, prepare_mcp_tools
from app.agent_runtime.mcp_tool_loader import _AuthInjectorInterceptor

PATH_SECRET = "sk-path-secret-123"


def _config(name: str, url: str, token: str) -> dict[str, Any]:
    return {
        "name": name,
        "mcp_tool_name": name,
        "mcp_server_url": url,
        "mcp_transport_headers": {"X-Tenant": url},
        "auth_config": {"api_token": token},
    }


@dataclass
class _Request:
    name: str
    args: dict[str, Any]

    def override(self, *, args: dict[str, Any]) -> _Request:
        return replace(self, args=args)


def _tool(name: str, calls: list[str], *, fail: bool = False) -> BaseTool:
    async def call(**_kwargs: Any) -> str:
        calls.append(name)
        if fail:
            raise ConnectionError("transport dropped")
        return name

    return StructuredTool.from_function(coroutine=call, name=name, description=name)


@pytest.mark.asyncio
async def test_auth_injection_is_scoped_per_server_and_names_are_opaque() -> None:
    interceptors: dict[str, list[Any]] = {}
    calls: list[str] = []

    async def discover(key: str, config: dict, chain: list) -> list[BaseTool]:
        interceptors[config["url"]] = chain
        return [_tool("search", calls)]

    configs = [
        _config("search", "https://a.test/mcp", "token-a"),
        _config("search", f"https://b.test/mcp/{PATH_SECRET}", "token-b"),
    ]
    prepared = await prepare_mcp_tools(
        configs, per_server_timeout=1.0, total_timeout=2.0, discover=discover
    )

    for config in configs:
        chain = interceptors[config["mcp_server_url"]]
        injector = next(i for i in chain if isinstance(i, _AuthInjectorInterceptor))
        seen: list[dict[str, Any]] = []

        async def handler(request: _Request, sink: list = seen) -> None:
            sink.append(request.args)

        await injector(_Request("search", {"q": "x"}), handler)
        assert seen == [{"api_token": config["auth_config"]["api_token"], "q": "x"}]
    names = sorted(item.tool_name for item in prepared.tools)
    assert len(set(names)) == 2 and all(n.startswith("srv_") for n in names)
    assert {item.mcp_tool_name for item in prepared.tools} == {"search"}
    assert PATH_SECRET not in repr([item.digest_fields() for item in prepared.tools])
    assert len({item.header_digest for item in prepared.tools}) == 2


@pytest.mark.asyncio
async def test_failed_call_is_sent_exactly_once() -> None:
    calls: list[str] = []

    async def discover(_key: str, _config: dict, _chain: list) -> list[BaseTool]:
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
    async def discover(_key: str, _config: dict, _chain: list) -> list[BaseTool]:
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
