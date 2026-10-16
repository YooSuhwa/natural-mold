"""Strict pre-marker MCP loading (parent Task 10, internal I13).

Unlike the cached legacy loader this performs exactly one uncached discovery
per selected server and never substitutes stub tools. Auth injection is
scoped per server (two servers exposing the same tool name never see each
other's injected arguments), wrapped tools send each call exactly once (no
retry re-send under a single dispatch permit), and every reason/descriptor
uses an opaque server id so URL-embedded secrets never reach errors, digests
or model-visible tool names.

Retained tools hold only their connection config: each permitted call opens a
fresh session (``tools/call`` only, never ``list_tools``), so no discovery
happens after the marker.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool

from app.agent_runtime.mcp_app_runtime import ResultMetaInterceptor
from app.agent_runtime.mcp_tool_loader import (
    _AuthInjectorInterceptor,
    _McpPlan,
    _plan_mcp_servers,
    _wrap_selected_tool,
)
from app.agent_runtime.runtime_hydration_guard import assert_pre_marker

DiscoverTools = Callable[[str, dict[str, Any], list[Any]], Awaitable[list[BaseTool]]]


class McpStrictLoadError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class StrictMcpTool:
    tool: BaseTool
    server_id: str
    mcp_tool_name: str
    config: dict[str, Any]
    headers: dict[str, Any]
    auth: dict[str, Any]


def opaque_server_id(server_key: str) -> str:
    return "srv_" + hashlib.sha256(server_key.encode()).hexdigest()[:16]


def _server_interceptors(plan: _McpPlan, key: str) -> list[Any]:
    auth = plan.server_tool_auth.get(key)
    interceptors: list[Any] = [ResultMetaInterceptor()]
    if auth:
        interceptors.insert(0, _AuthInjectorInterceptor(dict(auth)))
    return interceptors


async def default_discover(
    key: str, server_config: dict[str, Any], interceptors: list[Any]
) -> list[BaseTool]:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {key: server_config},  # type: ignore[arg-type]  # dict는 Connection TypedDict 호환
        tool_interceptors=interceptors,  # type: ignore[arg-type]
    )
    # ``get_tools`` opens one transient session, lists tools and closes it.
    return await client.get_tools(server_name=key)


def _disambiguate(loaded: list[StrictMcpTool]) -> None:
    counts: dict[str, int] = {}
    for item in loaded:
        counts[item.tool.name] = counts.get(item.tool.name, 0) + 1
    for item in loaded:
        if counts[item.tool.name] > 1:
            item.tool.name = f"{item.server_id}_{item.mcp_tool_name}"


async def load_selected_mcp_tools_strict(
    mcp_configs: list[dict], *, discover: DiscoverTools, call_timeout_seconds: float
) -> list[StrictMcpTool]:
    """Any discovery/cleanup failure or missing/hidden selected tool raises."""

    assert_pre_marker("load_selected_mcp_tools_strict")
    plan = _plan_mcp_servers(mcp_configs)
    loaded: list[StrictMcpTool] = []
    for key, config in plan.servers.items():
        server_id = opaque_server_id(key)
        server_auth = plan.server_tool_auth.get(key, {})
        auth_keys = {name for auth in server_auth.values() for name in auth}
        try:
            server_tools = await discover(key, config, _server_interceptors(plan, key))
        except Exception as error:  # noqa: BLE001 - every failure aborts preparation
            raise McpStrictLoadError(f"discovery:{server_id}") from error
        found = {t.name: t for t in server_tools if t.name in plan.tool_filter[key]}
        if set(found) != plan.tool_filter[key]:
            raise McpStrictLoadError(f"selected_tool_missing:{server_id}")
        for name in sorted(found):
            wrapped = _wrap_selected_tool(
                found[name],
                key,
                config,
                plan,
                call_timeout_seconds,
                auth_keys=auth_keys,
                max_retries=1,
            )
            if wrapped is None:
                raise McpStrictLoadError(f"selected_tool_hidden:{server_id}")
            loaded.append(
                StrictMcpTool(
                    tool=wrapped,
                    server_id=server_id,
                    mcp_tool_name=name,
                    config=plan.tool_configs[(key, name)],
                    headers=dict(config.get("headers") or {}),
                    auth=dict(server_auth.get(name) or {}),
                )
            )
    _disambiguate(loaded)
    return loaded
