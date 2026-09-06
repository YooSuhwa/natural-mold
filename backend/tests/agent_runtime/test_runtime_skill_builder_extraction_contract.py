"""Contracts for the extracted Skill Builder runtime preparation."""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agent_runtime import runtime_component_builder as builder
from app.agent_runtime.runtime_config import AgentConfig


def _config(**overrides: Any) -> AgentConfig:
    values: dict[str, Any] = {
        "provider": "provider",
        "model_name": "model",
        "api_key": None,
        "base_url": None,
        "system_prompt": "ignored",
        "tools_config": [],
        "thread_id": "builder-thread",
        "runtime_profile": "skill_builder",
        "skill_builder_session_id": "11111111-1111-4111-8111-111111111111",
        "draft_workspace_path": "skill-drafts/11111111-1111-4111-8111-111111111111",
        "skill_builder_consented_tools": ["test_skill_draft"],
    }
    values.update(overrides)
    return AgentConfig(**values)


def _tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    return tool


def test_skill_builder_facade_keeps_exact_signature() -> None:
    assert str(inspect.signature(builder._prepare_skill_builder_components)) == (
        "(cfg: 'AgentConfig', *, is_trigger_mode: 'bool', include_ask_user: 'bool', "
        "run_id: 'str | None' = None, scope_offload_backend: 'bool' = False) -> "
        "'RuntimeComponents'"
    )


@pytest.mark.asyncio
async def test_skill_builder_bindings_preserve_profile_and_security_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, Any] = {}
    model = MagicMock()
    backend = object()
    draft_tool = _tool("test_skill_draft")
    temporal_tool = _tool("current_datetime")
    ask_user = _tool("ask_user")

    session = object()

    def session_factory() -> object:
        return session

    def build_tools(**kwargs: Any) -> list[Any]:
        calls["tools"] = kwargs
        return [draft_tool]

    def append_temporal(tools: list[Any]) -> None:
        tools.append(temporal_tool)

    def permissions(**kwargs: Any) -> list[Any]:
        calls["permissions"] = kwargs
        return []

    def interrupt(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        calls["interrupt"] = kwargs
        return {
            "write_file": {},
            "edit_file": {},
            "test_skill_draft": {},
            "finalize_skill": {},
            "ask_user": {},
        }

    monkeypatch.setattr(
        builder,
        "load_skill_builder_prompt",
        lambda workspace: f"<builder:{workspace}>",
    )
    monkeypatch.setattr(
        builder,
        "_system_prompt_with_temporal_context",
        lambda prompt: f"<temporal>{prompt}</temporal>",
    )
    monkeypatch.setattr(builder, "_build_model_candidates", lambda _cfg: [model])
    monkeypatch.setattr(builder, "build_skill_builder_tools", build_tools)
    monkeypatch.setattr("app.database.async_session", session_factory)
    monkeypatch.setattr(builder, "_append_temporal_tools", append_temporal)
    monkeypatch.setattr(
        builder,
        "_build_default_reliability_middleware",
        lambda *_args, **_kwargs: ["reliability"],
    )
    monkeypatch.setattr(builder, "get_provider_middleware", lambda _provider: ["provider"])
    monkeypatch.setattr(builder, "_scoped_runtime_backend", lambda _cfg, **_kwargs: backend)
    monkeypatch.setattr(builder, "build_filesystem_permissions", permissions)
    monkeypatch.setattr(builder, "_interactive_tool_instruction_prompt", lambda: "<interactive>")
    monkeypatch.setattr(builder, "ask_user_tool", ask_user)
    monkeypatch.setattr(builder, "_build_interrupt_on_policy", interrupt)

    components = await builder._prepare_skill_builder_components(
        _config(),
        is_trigger_mode=False,
        include_ask_user=True,
        run_id="run",
        scope_offload_backend=True,
    )

    assert components.model is model
    assert components.backend is backend
    assert components.middleware == ["reliability", "provider"]
    assert [tool.name for tool in components.tools] == [
        "test_skill_draft",
        "current_datetime",
        "ask_user",
    ]
    assert components.system_prompt.endswith("\n\n<interactive>")
    assert calls["tools"]["session_factory"]() is session
    assert calls["tools"]["include_runtime_tools"] is True
    assert calls["permissions"]["draft_workspace_path"] == _config().draft_workspace_path
    assert calls["interrupt"] == {"include_ask_user": True, "is_trigger_mode": False}
    assert components.interrupt_on == {"finalize_skill": {}, "ask_user": {}}


@pytest.mark.asyncio
async def test_skill_builder_scoped_backend_still_requires_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder, "load_skill_builder_prompt", lambda _workspace: "prompt")
    monkeypatch.setattr(builder, "_system_prompt_with_temporal_context", lambda prompt: prompt)
    monkeypatch.setattr(builder, "_build_model_candidates", lambda _cfg: [MagicMock()])
    monkeypatch.setattr(builder, "build_skill_builder_tools", lambda **_kwargs: [])
    monkeypatch.setattr(builder, "_append_temporal_tools", lambda _tools: None)
    monkeypatch.setattr(
        builder,
        "_build_default_reliability_middleware",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(builder, "get_provider_middleware", lambda _provider: [])

    with pytest.raises(
        ValueError,
        match="run_id is required when scoped offload storage is enabled",
    ):
        await builder._prepare_skill_builder_components(
            _config(skill_builder_session_id=None),
            is_trigger_mode=False,
            include_ask_user=False,
            scope_offload_backend=True,
        )
