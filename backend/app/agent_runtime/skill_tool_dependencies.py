"""Runtime-only tool dependencies declared by attached skills.

Dependencies here are not persisted to ``agent_tools``. They only expand the
LangChain tool list for the current run so a marketplace skill can rely on a
stable tool name without cluttering the user's agent configuration.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.agent_runtime.tool_factory import _safe_tool_name
from app.tools.registry import registry

_DEPENDENCY_DESCRIPTIONS = {
    "tavily_search": "Hosted Tavily web search used by attached skills.",
    "research_search": "Search approved providers for cited research.",
}
SUPPORTED_SKILL_TOOL_DEPENDENCIES = frozenset(_DEPENDENCY_DESCRIPTIONS)


def _dependency_names(agent_skills: Sequence[Mapping[str, Any]]) -> list[str]:
    names: list[str] = []
    for skill in agent_skills:
        profile = skill.get("execution_profile")
        if not isinstance(profile, Mapping):
            continue
        raw_dependencies = profile.get("tool_dependencies")
        if not isinstance(raw_dependencies, Iterable) or isinstance(raw_dependencies, str):
            continue
        for item in raw_dependencies:
            if not isinstance(item, str):
                continue
            name = item.strip()
            if name and name not in names:
                names.append(name)
    return names


def build_skill_dependency_tool_configs(
    *,
    agent_skills: Sequence[Mapping[str, Any]],
    existing_tool_configs: Sequence[Mapping[str, Any]],
    user_id: str | None,
    agent_id: str | None,
    org_id: str | None = None,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    dependencies = _dependency_names(agent_skills)
    unsupported = sorted(set(dependencies) - SUPPORTED_SKILL_TOOL_DEPENDENCIES)
    if unsupported:
        joined = ", ".join(unsupported)
        raise ValueError(f"Unsupported skill tool dependency: {joined}")

    if not dependencies:
        return []

    # The model sees normalized registry names and the selected MCP server's
    # actual tool names. Check those names before injecting or reusing a tool.
    existing_names: set[str] = set()
    for config in existing_tool_configs:
        key = str(config.get("definition_key") or "")
        if config.get("mcp_server_url"):
            name = config.get("mcp_tool_name") or config.get("name")
        elif key.startswith("builtin:"):
            # Built-in tools have fixed names independent of their display alias.
            continue
        else:
            definition = registry.get(key)
            if definition is None or definition.runner is None:
                continue  # The runtime skips unregistered or non-executable tools.
            raw_name = config.get("name") or definition.display_name
            name = _safe_tool_name(str(raw_name), fallback=key)
        if name in dependencies:
            if name != key or config.get("mcp_server_url"):
                raise ValueError(f"Skill requires the registered tool named {name!r}")
            existing_names.add(name)

    injected: list[dict[str, Any]] = []
    for name in dependencies:
        if name in existing_names:
            continue
        injected.append(
            {
                "tool_id": f"skill-dependency:{name}",
                "definition_key": name,
                "name": name,
                "description": _DEPENDENCY_DESCRIPTIONS[name],
                "parameters": {},
                "credential_id": None,
                "credentials": None,
                "user_id": user_id,
                "org_id": org_id,
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "is_skill_dependency": True,
            }
        )
    return injected


__all__ = [
    "SUPPORTED_SKILL_TOOL_DEPENDENCIES",
    "build_skill_dependency_tool_configs",
]
