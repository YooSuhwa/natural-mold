"""Contracts for extracted standard and trigger runtime preparation."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent_runtime import runtime_component_builder as builder
from app.agent_runtime import runtime_preparation_support
from app.agent_runtime.runtime_config import AgentConfig


def test_preparation_legacy_wrappers_keep_exact_signatures() -> None:
    expected = {
        "_configured_recursion_limit": "(cfg: 'AgentConfig') -> 'int | None'",
        "_append_temporal_tools": "(tools: 'list[BaseTool]') -> 'None'",
        "_append_e2e_scripted_search_tool": "(tools: 'list[BaseTool]') -> 'None'",
        "_append_e2e_ui_data_demo_tool": "(tools: 'list[BaseTool]') -> 'None'",
        "_add_skill_secrets_to_run": "(skill_ctx: 'Any', cfg: 'AgentConfig') -> 'None'",
        "_selected_skill_slugs": ("(agent_skills: 'list[dict[str, Any]] | None') -> 'list[str]'"),
        "_scoped_runtime_backend": (
            "(cfg: 'AgentConfig', *, run_id: 'str | None') -> 'ScopedOffloadBackend'"
        ),
        "_prepare_runtime_components": (
            "(cfg: 'AgentConfig', *, is_trigger_mode: 'bool', include_ask_user: 'bool', "
            "include_agent_memory_file: 'bool', timings: 'dict[str, int] | None' = None, "
            "run_id: 'str | None' = None, scope_offload_backend: 'bool' = False) -> "
            "'RuntimeComponents'"
        ),
    }

    assert {name: str(inspect.signature(getattr(builder, name))) for name in expected} == expected


@pytest.mark.asyncio
async def test_preparation_bindings_are_resolved_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    first_model = MagicMock(name="first-model")
    second_model = MagicMock(name="second-model")

    def tool(name: str) -> MagicMock:
        value = MagicMock()
        value.name = name
        return value

    runtime_tool = tool("runtime")
    mcp_tool = tool("mcp")
    memory_tool = tool("memory")
    ask_user = tool("ask_user")

    async def build_mcp_tools(configs: list[dict[str, Any]]) -> list[Any]:
        calls.append("mcp")
        assert configs == [{"mcp_server_url": "https://mcp.test"}]
        return [mcp_tool]

    async def memory_policy(_cfg: AgentConfig, *, is_trigger_mode: bool) -> str:
        calls.append(f"memory-policy:{is_trigger_mode}")
        return "ask"

    async def load_memory(_cfg: AgentConfig) -> tuple[str, list[dict[str, Any]]]:
        calls.append("memory-context")
        return ("<memory-context>", [{"id": "memory-1"}])

    def append_named(name: str):
        def append(tools: list[Any]) -> None:
            calls.append(name)
            tools.append(tool(name))

        return append

    def permissions(**kwargs: Any) -> list[Any]:
        calls.append("permissions")
        assert kwargs["thread_id"] == "binding-thread"
        return []

    def interrupt(
        _configs: Any,
        tools: list[Any],
        *,
        include_ask_user: bool,
        is_trigger_mode: bool,
    ) -> dict[str, Any]:
        calls.append(f"interrupt:{include_ask_user}:{is_trigger_mode}")
        return {"observed": [item.name for item in tools]}

    monkeypatch.setattr(builder, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(
        builder, "_system_prompt_with_temporal_context", lambda value: value + "<t>"
    )
    monkeypatch.setattr(builder, "_artifact_file_instruction_prompt", lambda _thread: "<a>")
    monkeypatch.setattr(builder, "_interactive_tool_instruction_prompt", lambda: "<i>")
    monkeypatch.setattr(builder, "_build_model_candidates", lambda _cfg: [first_model])
    monkeypatch.setattr(builder, "build_skill_dependency_tool_configs", lambda **_kwargs: [])
    monkeypatch.setattr(builder, "create_tool_for_runtime", lambda _config: runtime_tool)
    monkeypatch.setattr(builder, "_build_mcp_tools", build_mcp_tools)
    monkeypatch.setattr(builder, "_append_temporal_tools", append_named("temporal"))
    monkeypatch.setattr(
        builder,
        "_append_e2e_scripted_search_tool",
        append_named("scripted-search"),
    )
    monkeypatch.setattr(builder, "_append_e2e_ui_data_demo_tool", append_named("ui-data"))
    monkeypatch.setattr(builder, "_memory_write_policy_for_run", memory_policy)
    monkeypatch.setattr(builder, "build_memory_tools", lambda **_kwargs: [memory_tool])
    monkeypatch.setattr(builder, "_resolve_middleware_model_params", lambda configs, _keys: configs)
    monkeypatch.setattr(
        builder,
        "_build_default_reliability_middleware",
        lambda *_args, **_kwargs: ["reliability"],
    )
    monkeypatch.setattr(builder, "build_middleware_instances", lambda _configs: ["configured"])
    monkeypatch.setattr(builder, "get_provider_middleware", lambda _provider: ["provider"])
    monkeypatch.setattr(builder, "_load_memory_context", load_memory)
    monkeypatch.setattr(builder, "build_filesystem_permissions", permissions)
    monkeypatch.setattr(builder, "_build_interrupt_on_policy", interrupt)
    monkeypatch.setattr(builder, "ask_user_tool", ask_user)

    config = AgentConfig(
        provider="provider",
        model_name="model",
        api_key=None,
        base_url=None,
        system_prompt="<base>",
        tools_config=[
            {"name": "runtime"},
            {"mcp_server_url": "https://mcp.test"},
        ],
        thread_id="binding-thread",
        middleware_configs=[{"type": "custom"}],
        agent_id="00000000-0000-4000-8000-000000000001",
        user_id="00000000-0000-4000-8000-000000000002",
    )
    standard = await builder._prepare_runtime_components(
        config,
        is_trigger_mode=False,
        include_ask_user=True,
        include_agent_memory_file=True,
    )

    monkeypatch.setattr(builder, "_build_model_candidates", lambda _cfg: [second_model])
    trigger = await builder._prepare_runtime_components(
        config,
        is_trigger_mode=True,
        include_ask_user=False,
        include_agent_memory_file=False,
    )

    assert standard.model is first_model
    assert trigger.model is second_model
    assert [item.name for item in standard.tools] == [
        "runtime",
        "mcp",
        "temporal",
        "scripted-search",
        "ui-data",
        "memory",
        "ask_user",
    ]
    assert [item.name for item in trigger.tools] == [
        "runtime",
        "mcp",
        "temporal",
        "scripted-search",
        "ui-data",
        "memory",
    ]
    assert standard.middleware == ["reliability", "configured", "provider"]
    assert "<t>\n\n<a>\n\n<i>" in standard.system_prompt
    assert "<memory-context>" in standard.system_prompt
    assert "<i>" not in trigger.system_prompt
    assert "memory-context" in calls
    assert "interrupt:True:False" in calls
    assert "interrupt:False:True" in calls


@pytest.mark.asyncio
async def test_preparation_skill_builder_dispatch_uses_live_facade_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    components = MagicMock()
    prepare_skill_builder = AsyncMock(return_value=components)
    monkeypatch.setattr(builder, "_prepare_skill_builder_components", prepare_skill_builder)
    config = AgentConfig(
        provider="provider",
        model_name="model",
        api_key=None,
        base_url=None,
        system_prompt="prompt",
        tools_config=[],
        thread_id="skill-builder-thread",
        runtime_profile="skill_builder",
    )

    result = await builder._prepare_runtime_components(
        config,
        is_trigger_mode=False,
        include_ask_user=False,
        include_agent_memory_file=True,
    )

    assert result is components
    prepare_skill_builder.assert_awaited_once_with(
        config,
        is_trigger_mode=False,
        include_ask_user=False,
        run_id=None,
        scope_offload_backend=False,
    )


def test_operation_time_helpers_resolve_original_owning_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    monkeypatch.setattr("app.database.async_session", lambda: session)
    monkeypatch.setattr("app.skills.prompt.build_skills_prompt", lambda _skills: "patched")

    assert runtime_preparation_support.runtime_db_session_factory() is session
    assert runtime_preparation_support.build_runtime_skills_prompt([]) == "patched"


@pytest.mark.asyncio
async def test_preparation_propagates_existing_failure_without_factory_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_factory = MagicMock()
    monkeypatch.setattr(builder, "build_agent", graph_factory)
    monkeypatch.setattr(
        builder,
        "_build_model_candidates",
        MagicMock(side_effect=ValueError("invalid model configuration")),
    )

    with pytest.raises(ValueError, match="invalid model configuration"):
        await builder._prepare_agent(
            AgentConfig(
                provider="provider",
                model_name="model",
                api_key=None,
                base_url=None,
                system_prompt="prompt",
                tools_config=[],
                thread_id="failure-thread",
            ),
            messages_history=[],
        )

    graph_factory.assert_not_called()
