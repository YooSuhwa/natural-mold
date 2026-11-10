"""Resource keys: what a tool setting is attached to (tool-governance-plan 3.2).

A setting names a tool by its row, never by the name the model calls it by: two MCP servers
may expose the same tool name, and a run then renames both (``srv_…``).

* ``mcp:<mcp_tools.id>`` — an MCP tool; ``mcp_server:<mcp_servers.id>`` — its server;
* ``tool:<tools.id>`` — a registered tool;
* ``builtin:<name>`` — a tool the product builds itself (``web_search``, ``write_file``, …);
* ``skill:<skills.id>`` — a skill, run through the one tool ``execute_in_skill``;
  ``agent:<agents.id>`` — a child agent, reached through the one tool ``task``. These are
  *parts* of a tool: which one a call means is read from its arguments (plan 3.8).

The key travels in the tool object's metadata next to its risk level, with the name the
tool had before any run-time rename (settings saved by name are still read through it).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from langchain_core.tools import BaseTool

KEY_METADATA: Final = "moldy_resource"
PARTS_METADATA: Final = "moldy_parts"
SKILL_TOOL: Final = "execute_in_skill"
DELEGATION_TOOL: Final = "task"
PREFIXES: Final = ("mcp:", "mcp_server:", "tool:", "builtin:", "skill:", "agent:")


@dataclass(frozen=True, slots=True)
class ToolRef:
    """One tool as a setting sees it."""

    key: str
    name: str  # the name before a run-time rename (what older settings were saved under)
    server: str | None = None  # ``mcp_server:<id>`` for an MCP tool
    # Where it comes from, for people (the MCP server's name); not part of its identity.
    source: str | None = field(default=None, compare=False)
    # The tool's own approval default (``resolved_default``); a setting, not identity.
    default: Mapping[str, Any] | None = field(default=None, compare=False)


def is_key(value: str) -> bool:
    return value.startswith(PREFIXES)


def builtin_key(name: str) -> str:
    return f"builtin:{name}"


def server_key(server_id: Any) -> str | None:
    return f"mcp_server:{server_id}" if server_id else None


def resolved_default(
    tool: Mapping[str, Any] | None, server: Mapping[str, Any] | None = None
) -> dict[str, Any] | None:
    """The default a tool carries: its own, else its server's (plan 3.3: tool → server)."""
    for layer, value in (("tool", tool), ("server", server)):
        if isinstance(value, Mapping) and value.get("mode"):
            return {"mode": value["mode"], "rule": value.get("rule"), "layer": layer}
    return None


def default_config(
    tool: Mapping[str, Any] | None, server: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The ``approval_default`` entry of a tool config; absent when no default is set, so a
    config without one is byte-for-byte what it was before defaults existed."""
    found = resolved_default(tool, server)
    return {"approval_default": found} if found else {}


def ref_from_config(config: Mapping[str, Any], *, name: str) -> ToolRef:
    """The ref of a ``build_tools_config`` entry; ``name`` is its un-renamed run-time name."""
    definition = str(config.get("definition_key") or "")
    default = config.get("approval_default")
    default = default if isinstance(default, Mapping) else None
    if definition == "mcp":
        tool_id = config.get("mcp_tool_id")
        if tool_id:
            return ToolRef(
                f"mcp:{tool_id}",
                name,
                server_key(config.get("mcp_server_id")),
                config.get("mcp_server_name"),
                default,
            )
        return ToolRef(builtin_key(name), name)
    tool_id = config.get("tool_id")
    if definition.startswith("builtin:") or not tool_id:
        return ToolRef(builtin_key(name), name)
    return ToolRef(f"tool:{tool_id}", name, default=default)


def attach_tool_ref(tool: BaseTool, ref: ToolRef) -> BaseTool:
    current = dict(getattr(tool, "metadata", None) or {})
    current[KEY_METADATA] = {
        "key": ref.key,
        "name": ref.name,
        "server": ref.server,
        "default": dict(ref.default) if ref.default else None,
    }
    try:
        tool.metadata = current
    except Exception:  # noqa: BLE001 - pydantic model fallback (as ``attach_tool_risk``)
        object.__setattr__(tool, "metadata", current)
    return tool


def ref_of(tool: BaseTool | str) -> ToolRef:
    """A tool's ref; one without metadata (Deep Agents' own tools) is a builtin by name."""
    if isinstance(tool, str):
        return ToolRef(builtin_key(tool), tool)
    raw = (getattr(tool, "metadata", None) or {}).get(KEY_METADATA)
    if isinstance(raw, Mapping) and raw.get("key"):
        default = raw.get("default")
        return ToolRef(
            str(raw["key"]),
            str(raw.get("name") or tool.name),
            raw.get("server"),
            default=default if isinstance(default, Mapping) else None,
        )
    return ToolRef(builtin_key(tool.name), tool.name)


def skill_ref(skill: Mapping[str, Any]) -> ToolRef:
    """A skill as a part of ``execute_in_skill``; ``skill`` is its runtime dict."""
    return ToolRef(
        f"skill:{skill['id']}",
        str(skill.get("slug") or skill.get("name") or skill["id"]),
        builtin_key(SKILL_TOOL),
        skill.get("name"),
        resolved_default(skill.get("approval_default")),
    )


def agent_ref(agent_id: Any, name: str) -> ToolRef:
    """A child agent as a part of ``task``."""
    return ToolRef(f"agent:{agent_id}", name, builtin_key(DELEGATION_TOOL), name)


def attach_parts(tool: BaseTool, parts: Mapping[str, ToolRef]) -> BaseTool:
    """``parts``: the value a call's argument carries (a skill's slug) → that part's ref."""
    current = dict(getattr(tool, "metadata", None) or {})
    current[PARTS_METADATA] = dict(parts)
    try:
        tool.metadata = current
    except Exception:  # noqa: BLE001 - pydantic model fallback (as ``attach_tool_risk``)
        object.__setattr__(tool, "metadata", current)
    return tool


def parts_of(tool: BaseTool | str) -> dict[str, ToolRef]:
    if isinstance(tool, str):
        return {}
    raw = (getattr(tool, "metadata", None) or {}).get(PARTS_METADATA)
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, ToolRef)}


def part_named(tool_name: str, args: Any) -> str | None:
    """Which part a call of ``tool_name`` means, from its arguments (as the tool reads them)."""
    if not isinstance(args, Mapping):
        return None
    if tool_name == SKILL_TOOL:
        directory = args.get("skill_directory")
        if not isinstance(directory, str):
            return None
        # The very expression ``execute_in_skill`` resolves the skill with.
        return Path(directory.strip("/")).name or None
    if tool_name == DELEGATION_TOOL:
        child = args.get("subagent_type")
        return child if isinstance(child, str) else None
    return None


def ambiguous_names(refs: Iterable[ToolRef]) -> frozenset[str]:
    """Names more than one of these tools carries (a name setting cannot tell them apart)."""
    seen: dict[str, str] = {}
    twice: set[str] = set()
    for ref in refs:
        if seen.setdefault(ref.name, ref.key) != ref.key:
            twice.add(ref.name)
    return frozenset(twice)


def resolve(refs: Iterable[ToolRef], value: str) -> ToolRef | None:
    """The one tool ``value`` names: a key, or a name only one of ``refs`` carries."""
    listed = list(refs)
    for ref in listed:
        if ref.key == value:
            return ref
    named = {ref.key: ref for ref in listed if ref.name == value}
    return next(iter(named.values())) if len(named) == 1 else None


__all__ = [
    "KEY_METADATA",
    "DELEGATION_TOOL",
    "PARTS_METADATA",
    "SKILL_TOOL",
    "ToolRef",
    "agent_ref",
    "ambiguous_names",
    "attach_parts",
    "attach_tool_ref",
    "part_named",
    "parts_of",
    "skill_ref",
    "builtin_key",
    "default_config",
    "is_key",
    "ref_from_config",
    "ref_of",
    "resolve",
    "resolved_default",
    "server_key",
]
