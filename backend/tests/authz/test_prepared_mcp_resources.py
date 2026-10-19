"""Bounded pre-marker MCP preparation: one discovery, frozen descriptors, no stubs."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest
from langchain_core.tools import BaseTool, StructuredTool

from app.agent_runtime.mcp_preparation import McpPreparationError, prepare_mcp_tools

SECRET = "Bearer mcp-secret-token"


def _config(name: str, url: str = "https://mcp.test/a") -> dict[str, Any]:
    return {
        "name": name,
        "mcp_tool_name": name,
        "mcp_server_url": url,
        "mcp_transport_headers": {"Authorization": SECRET},
        "connection_id": "conn-1",
    }


class FakeServer:
    def __init__(self, names: tuple[str, ...], *, delay: float = 0.0, close_error: bool = False):
        self.names = names
        self.delay = delay
        self.close_error = close_error
        self.discoveries = 0
        self.open_sessions = 0
        self.tool_calls = 0

    @asynccontextmanager
    async def _session(self):
        self.open_sessions += 1
        try:
            yield
        finally:
            self.open_sessions -= 1
            if self.close_error:
                raise RuntimeError("session close failed")

    def _tool(self, name: str) -> BaseTool:
        async def call(**_kwargs: Any) -> str:
            self.tool_calls += 1
            return name

        return StructuredTool.from_function(coroutine=call, name=name, description=name)

    async def discover(self, _key: str, _config: dict, _interceptors: list) -> list[BaseTool]:
        self.discoveries += 1
        async with self._session():
            if self.delay:
                await asyncio.sleep(self.delay)
            return [self._tool(name) for name in self.names]


async def _prepare(server: FakeServer, configs: list[dict], **kwargs: Any):
    return await prepare_mcp_tools(
        configs,
        per_server_timeout=kwargs.get("per_server", 1.0),
        total_timeout=kwargs.get("total", 2.0),
        discover=server.discover,
    )


@pytest.mark.asyncio
async def test_selected_fake_mcp_tool_is_completely_resolved() -> None:
    server = FakeServer(("search", "fetch", "unselected"))
    configs = [_config("search"), _config("fetch")]

    prepared = await _prepare(server, configs)
    again = await _prepare(FakeServer(("search", "fetch", "unselected")), configs)

    assert prepared.discovery_count == 1 == server.discoveries
    assert server.open_sessions == 0
    assert server.tool_calls == 0
    assert sorted(item.mcp_tool_name for item in prepared.tools) == ["fetch", "search"]
    assert all(item.secret_reference_ids == ("conn-1",) for item in prepared.tools)
    assert prepared.digest == again.digest
    assert SECRET not in repr(prepared) and SECRET not in prepared.digest
    assert prepared.descriptor_ids == {item.descriptor_id for item in prepared.tools}


@pytest.mark.asyncio
async def test_missing_selected_tool_aborts_without_stub() -> None:
    server = FakeServer(("search",))

    with pytest.raises(McpPreparationError) as error:
        await _prepare(server, [_config("search"), _config("absent")])

    assert error.value.code == "mcp_preparation_failed"
    assert error.value.reason.startswith("selected_tool_missing")


@pytest.mark.asyncio
@pytest.mark.parametrize(("per_server", "total"), [(0.05, 5.0), (5.0, 0.05)])
async def test_timeout_cancel_and_cleanup_failure_roll_back(
    per_server: float, total: float
) -> None:
    slow = FakeServer(("search",), delay=1.0)
    with pytest.raises(McpPreparationError) as timeout:
        await _prepare(slow, [_config("search")], per_server=per_server, total=total)
    assert timeout.value.reason == "timeout"
    assert slow.open_sessions == 0

    broken = FakeServer(("search",), close_error=True)
    with pytest.raises(McpPreparationError) as cleanup:
        await _prepare(broken, [_config("search")])
    assert cleanup.value.reason.startswith("discovery")

    blocked = FakeServer(("search",), delay=5.0)
    task = asyncio.create_task(_prepare(blocked, [_config("search")], per_server=10, total=10))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert blocked.open_sessions == 0


@pytest.mark.asyncio
async def test_each_selected_server_is_discovered_exactly_once() -> None:
    first = FakeServer(("search",))
    configs = [_config("search", "https://mcp.test/a"), _config("search2", "https://mcp.test/b")]
    calls: list[str] = []

    async def discover(key: str, config: dict, interceptors: list) -> list[BaseTool]:
        calls.append(config["url"])
        names = ("search",) if config["url"].endswith("/a") else ("search2",)
        server = FakeServer(names)
        return await server.discover(key, config, interceptors)

    prepared = await prepare_mcp_tools(
        configs, per_server_timeout=1.0, total_timeout=2.0, discover=discover
    )

    assert sorted(calls) == ["https://mcp.test/a", "https://mcp.test/b"]
    assert prepared.discovery_count == 2
    assert first.discoveries == 0
