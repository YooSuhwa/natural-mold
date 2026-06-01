"""Runtime-only tool dependencies declared by attached skills.

Dependencies here are not persisted to ``agent_tools``. They only expand the
LangChain tool list for the current run so a marketplace skill can rely on a
stable tool name without cluttering the user's agent configuration.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

SUPPORTED_SKILL_TOOL_DEPENDENCIES = {"tavily_search"}


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
) -> list[dict[str, Any]]:
    dependencies = _dependency_names(agent_skills)
    unsupported = sorted(set(dependencies) - SUPPORTED_SKILL_TOOL_DEPENDENCIES)
    if unsupported:
        joined = ", ".join(unsupported)
        raise ValueError(f"Unsupported skill tool dependency: {joined}")

    existing_names = {
        str(config.get("name", "")).strip()
        for config in existing_tool_configs
        if str(config.get("name", "")).strip()
    }

    injected: list[dict[str, Any]] = []
    for name in dependencies:
        if name in existing_names:
            continue
        injected.append(
            {
                "tool_id": f"skill-dependency:{name}",
                "definition_key": name,
                "name": name,
                "description": "Hosted Tavily web search used by attached skills.",
                "parameters": {},
                "credential_id": None,
                "credentials": None,
                "user_id": user_id,
                "agent_id": agent_id,
                "is_skill_dependency": True,
            }
        )
    return injected


__all__ = [
    "SUPPORTED_SKILL_TOOL_DEPENDENCIES",
    "build_skill_dependency_tool_configs",
]
