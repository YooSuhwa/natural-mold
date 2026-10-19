"""Selected tools plus trusted origins for prepared-run inputs (parent Task 10).

Converts the legacy ``tools_config`` list (``build_tools_config``) into lease
inputs before the marker: registry/builtin tools are built once with their
already-resolved credentials and declared with a ``ToolOrigin``; MCP entries
are handed to the I13 strict discovery (root only); credential/MCP server IDs
become non-secret digest references. Temporal builtins (and the E2E scripted
helpers when that gate is on) are appended exactly as the legacy preparation
does. ``ask_user`` is an app tool that only interrupts run-local graph state.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool

from app.agent_runtime.prepared_run_input_policy import unsupported
from app.agent_runtime.runtime_policy_capabilities import STORED_RESERVED_TOOL_NAMES
from app.agent_runtime.tool_access_policy import ToolOrigin
from app.agent_runtime.tool_factory import create_builtin_tool
from app.config import settings

TEMPORAL_BUILTIN_KEYS = ("builtin:current_datetime", "builtin:resolve_relative_date")
E2E_BUILTIN_KEYS = ("builtin:e2e_scripted_search", "builtin:e2e_ui_data_demo")
ASK_USER_ORIGIN_KEY = "ask_user"


@dataclass(frozen=True, slots=True)
class PreparedToolSet:
    tools: tuple[BaseTool, ...]
    origins: tuple[ToolOrigin, ...]
    mcp_configs: tuple[dict[str, Any], ...]
    secret_reference_ids: tuple[str, ...]

    @property
    def names(self) -> frozenset[str]:
        return frozenset(tool.name for tool in self.tools)


def builtin_keys() -> tuple[str, ...]:
    extra = E2E_BUILTIN_KEYS if settings.e2e_scripted_model_enabled else ()
    return (*TEMPORAL_BUILTIN_KEYS, *extra)


def _references(config: Mapping[str, Any]) -> list[str]:
    refs = []
    if config.get("credential_id"):
        refs.append(f"credential:{config['credential_id']}")
    if config.get("mcp_server_id"):
        refs.append(f"mcp_server:{config['mcp_server_id']}")
    return refs


def prepared_tool_set(
    tool_configs: Iterable[Mapping[str, Any]],
    *,
    create_tool: Callable[[dict[str, Any]], BaseTool | None],
    allow_mcp: bool,
    stored_policy: bool,
    ask_user_tool: BaseTool | None = None,
    subject: str = "",
) -> PreparedToolSet:
    """Build the selected tool set once, before the marker, with closed origins."""

    tools: list[BaseTool] = []
    origins: list[ToolOrigin] = []
    mcp: list[dict[str, Any]] = []
    refs: list[str] = []
    for config in tool_configs:
        refs.extend(_references(config))
        if config.get("mcp_server_url"):
            if not allow_mcp:
                raise unsupported(f"{subject}child_mcp")
            mcp.append(dict(config))
            continue
        tool = create_tool(dict(config))
        if tool is None:
            continue
        key = str(config.get("definition_key") or "")
        source = "builtin" if key.startswith("builtin:") else "registry"
        tools.append(tool)
        origins.append(ToolOrigin(tool.name, source, key))
    for key in builtin_keys():
        tool = create_builtin_tool(key)
        if tool is not None and tool.name not in {item.name for item in tools}:
            tools.append(tool)
            origins.append(ToolOrigin(tool.name, "builtin", key))
    if stored_policy:
        kept = [i for i, tool in enumerate(tools) if tool.name not in STORED_RESERVED_TOOL_NAMES]
        tools, origins = [tools[i] for i in kept], [origins[i] for i in kept]
    if ask_user_tool is not None:
        tools.append(ask_user_tool)
        origins.append(
            ToolOrigin(ask_user_tool.name, "app", ASK_USER_ORIGIN_KEY, access_class="run_output")
        )
    return PreparedToolSet(tuple(tools), tuple(origins), tuple(mcp), tuple(refs))


def merged_origins(sets: Sequence[PreparedToolSet]) -> tuple[ToolOrigin, ...]:
    """One origin per tool name across participants; conflicting origins reject."""

    merged: dict[str, ToolOrigin] = {}
    for tool_set in sets:
        for origin in tool_set.origins:
            seen = merged.setdefault(origin.name, origin)
            if seen != origin:
                raise unsupported(f"tool_origin_conflict:{origin.name}")
    return tuple(merged.values())
