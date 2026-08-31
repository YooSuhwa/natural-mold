"""Hermetic characterisation of component-backed runtime profiles."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from deepagents.middleware.filesystem import FilesystemPermission
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.tools import BaseTool, StructuredTool

from app.agent_runtime.filesystem_permissions import (
    FilesystemOperation,
    calculate_filesystem_access,
)
from app.agent_runtime.runtime_config import AgentConfig, RuntimeComponents
from tests.agent_runtime.runtime_contract_helpers import (
    JSONValue,
    contract_diff_paths,
    load_contract_fixture,
)

_FIXTURE = "runtime_profiles_v1.json"
_BUILDER_SESSION_ID = "00000000-0000-4000-8000-000000000008"


class _RetryProfileMiddleware(AgentMiddleware):
    @property
    def name(self) -> str:
        return "retry"


class _ProviderProfileMiddleware(AgentMiddleware):
    @property
    def name(self) -> str:
        return "provider"


def _tool_result() -> str:
    return "profile-result"


def _tool(name: str) -> BaseTool:
    return StructuredTool.from_function(
        _tool_result,
        name=name,
        description=f"Hermetic {name} profile tool.",
    )


def _json_strings(values: Sequence[str]) -> list[JSONValue]:
    return list(values)


def _permission_access(
    permissions: list[FilesystemPermission],
    operation: FilesystemOperation,
    path: str,
) -> str:
    access, _ = calculate_filesystem_access(permissions, operation, path)
    return access


def _compiled_nodes(components: RuntimeComponents, *, name: str) -> list[JSONValue]:
    from app.agent_runtime.runtime_component_builder import build_agent

    graph = build_agent(
        components.model,
        components.tools,
        components.system_prompt,
        middleware=components.middleware or None,
        interrupt_on=components.interrupt_on,
        backend=components.backend,
        skills=components.skills_sources,
        memory=components.memory_sources,
        permissions=components.permissions,
        name=name,
    )
    return _json_strings(sorted(graph.nodes))


def _component_manifest(components: RuntimeComponents, *, profile: str) -> dict[str, JSONValue]:
    prompt = components.system_prompt
    permissions = components.permissions
    thread_id = "profile-thread"
    manifest: dict[str, JSONValue] = {
        "compiled_nodes": _compiled_nodes(components, name=f"contract_{profile}"),
        "tools": _json_strings([str(tool.name) for tool in components.tools]),
        "middleware": _json_strings([str(item.name) for item in components.middleware]),
        "interrupt_tools": _json_strings(sorted((components.interrupt_on or {}).keys())),
        "skills_sources": _json_strings(components.skills_sources or []),
        "memory_sources": _json_strings(components.memory_sources or []),
        "permissions": {
            "conversation_write": _permission_access(
                permissions, "write", f"/conversations/{thread_id}/report.txt"
            ),
            "runtime_read": _permission_access(permissions, "read", "/runtime/other/SKILL.md"),
        },
        "prompt": {
            "has_artifact_conversation_path": f"/conversations/{thread_id}/" in prompt,
            "has_ask_user_tool_marker": "ask_user" in prompt,
        },
    }
    if profile == "skill_builder":
        manifest["permissions"] = {
            "draft_write": _permission_access(
                permissions, "write", f"/skill-drafts/{_BUILDER_SESSION_ID}/SKILL.md"
            ),
            "conversation_write": _permission_access(
                permissions, "write", f"/conversations/{thread_id}/report.txt"
            ),
        }
        manifest["prompt"] = {
            "has_draft_workspace_path": f"/skill-drafts/{_BUILDER_SESSION_ID}" in prompt,
            "has_ask_user_tool_marker": "ask_user" in prompt,
        }
    return manifest


def _configure_component_fakes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.agent_runtime import runtime_component_builder as rcb

    model = FakeListChatModel(responses=["profile-response"])
    monkeypatch.setattr(rcb, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(rcb, "_build_model_candidates", lambda _cfg: [model])
    monkeypatch.setattr(
        rcb,
        "create_builtin_tool",
        lambda key: (
            _tool(name)
            if (
                name := {
                    "builtin:current_datetime": "current_datetime",
                    "builtin:resolve_relative_date": "resolve_relative_date",
                }.get(key)
            )
            else None
        ),
    )
    monkeypatch.setattr(rcb, "build_memory_tools", lambda **_kwargs: [_tool("memory_search")])
    monkeypatch.setattr(
        rcb,
        "_build_default_reliability_middleware",
        lambda *_args, **_kwargs: [_RetryProfileMiddleware()],
    )
    monkeypatch.setattr(rcb, "build_middleware_instances", lambda _configs: [])
    monkeypatch.setattr(
        rcb, "get_provider_middleware", lambda _provider: [_ProviderProfileMiddleware()]
    )
    monkeypatch.setattr(rcb, "_load_memory_context", AsyncMock(return_value=("", [])))
    monkeypatch.setattr(rcb, "_memory_write_policy_for_run", AsyncMock(return_value="auto"))
    monkeypatch.setattr(
        rcb,
        "build_skill_builder_tools",
        lambda **_kwargs: [_tool("validate_skill"), _tool("generate_evals")],
    )
    monkeypatch.setattr(
        rcb,
        "load_skill_builder_prompt",
        lambda workspace: f"<skill-builder workspace=/{workspace}>",
    )


async def _collect_component_profiles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> dict[str, JSONValue]:
    from app.agent_runtime import runtime_component_builder as rcb

    _configure_component_fakes(monkeypatch, tmp_path)
    common = {
        "provider": "openai",
        "model_name": "profile-model",
        "api_key": None,
        "base_url": None,
        "system_prompt": "<profile-system>",
        "tools_config": [],
        "thread_id": "profile-thread",
        "agent_id": "profile-agent",
        "user_id": "profile-user",
    }
    standard = await rcb._prepare_runtime_components(
        AgentConfig(**common),
        is_trigger_mode=False,
        include_ask_user=True,
        include_agent_memory_file=True,
    )
    trigger = await rcb._prepare_runtime_components(
        AgentConfig(**common),
        is_trigger_mode=True,
        include_ask_user=False,
        include_agent_memory_file=True,
    )
    skill_builder = await rcb._prepare_runtime_components(
        AgentConfig(
            **common,
            runtime_profile="skill_builder",
            skill_builder_session_id=_BUILDER_SESSION_ID,
            draft_workspace_path=f"skill-drafts/{_BUILDER_SESSION_ID}",
        ),
        is_trigger_mode=False,
        include_ask_user=True,
        include_agent_memory_file=True,
    )
    return {
        "standard": _component_manifest(standard, profile="standard"),
        "skill_builder": _component_manifest(skill_builder, profile="skill_builder"),
        "trigger": _component_manifest(trigger, profile="trigger"),
    }


def _assert_component_contract(actual: dict[str, JSONValue]) -> None:
    fixture = load_contract_fixture(_FIXTURE)
    expected = {name: fixture[name] for name in actual}
    differences = contract_diff_paths(expected, actual)
    assert not differences, f"contract drift at: {', '.join(differences[:20])}"


@pytest.mark.asyncio
async def test_component_profiles_compile_to_reviewed_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _assert_component_contract(await _collect_component_profiles(monkeypatch, tmp_path))


@pytest.mark.asyncio
async def test_runtime_profile_contract_rejects_interrupt_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mutated = deepcopy(await _collect_component_profiles(monkeypatch, tmp_path))
    trigger = mutated["trigger"]
    assert isinstance(trigger, dict)
    trigger["interrupt_tools"] = ["ask_user"]

    with pytest.raises(AssertionError, match=r"\$\.trigger\.interrupt_tools"):
        _assert_component_contract(mutated)
