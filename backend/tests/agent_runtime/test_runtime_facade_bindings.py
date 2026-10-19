"""Public compatibility facades resolve patched collaborators at call time.

Each test makes one successful call through the public facade, replaces the
collaborators on the facade module, and proves the next call observes the
replacement objects (would fail if an extracted module cached the first
binding at import or first-call time).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.tools import StructuredTool

from app.agent_runtime import checkpointer, executor, model_factory
from app.agent_runtime import runtime_component_builder as builder
from app.agent_runtime.runtime_config import AgentConfig
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID


def _tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(lambda: name, name=name, description=f"{name} tool")


def _config() -> AgentConfig:
    return AgentConfig(
        org_context=unit_context(TEST_USER_ID),
        provider="provider",
        model_name="model",
        api_key=None,
        base_url=None,
        system_prompt="<base>",
        tools_config=[{"name": "runtime"}],
        thread_id="facade-binding-thread",
        subagents_config=[{"name": "child", "description": "d", "system_prompt": "p"}],
    )


def _install_runtime(
    monkeypatch: pytest.MonkeyPatch, *, label: str, captured: list[dict[str, Any]]
) -> dict[str, Any]:
    model = FakeListChatModel(responses=[label])
    tool = _tool(f"tool-{label}")
    saver = MagicMock(name=f"saver-{label}")
    messages = [MagicMock(name=f"message-{label}")]

    def create_deep_agent(**kwargs: Any) -> str:
        captured.append({"label": label, **kwargs})
        return f"agent-{label}"

    async def no_mcp(_configs: list[dict[str, Any]]) -> list[Any]:
        return []

    async def memory_off(*_args: Any, **_kwargs: Any) -> str:
        return "off"

    async def no_memory(_cfg: AgentConfig) -> tuple[str, list[dict[str, Any]]]:
        return ("", [])

    monkeypatch.setattr(builder, "create_deep_agent", create_deep_agent)
    monkeypatch.setattr(builder, "_build_model_candidates", lambda _cfg: [model])
    monkeypatch.setattr(builder, "create_tool_for_runtime", lambda _config: tool)
    monkeypatch.setattr(builder, "_build_mcp_tools", no_mcp)
    monkeypatch.setattr(builder, "_memory_write_policy_for_run", memory_off)
    monkeypatch.setattr(builder, "_load_memory_context", no_memory)
    monkeypatch.setattr(builder, "_append_temporal_tools", lambda _tools: None)
    monkeypatch.setattr(builder, "_append_e2e_scripted_search_tool", lambda _tools: None)
    monkeypatch.setattr(builder, "_append_e2e_ui_data_demo_tool", lambda _tools: None)
    monkeypatch.setattr(builder, "get_provider_middleware", lambda _provider: [])
    monkeypatch.setattr(builder, "convert_to_langchain_messages", lambda _history: messages)
    monkeypatch.setattr(checkpointer, "get_checkpointer", lambda: saver)
    return {"model": model, "tool": tool, "saver": saver, "messages": messages}


@pytest.mark.asyncio
async def test_public_runtime_facade_observes_replacement_after_first_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []
    first = _install_runtime(monkeypatch, label="first", captured=captured)
    first_agent, first_messages, _ = await executor._prepare_agent(_config(), messages_history=[])

    second = _install_runtime(monkeypatch, label="second", captured=captured)
    second_agent, second_messages, config = await builder._prepare_agent(
        _config(), messages_history=[]
    )

    assert (first_agent, second_agent) == ("agent-first", "agent-second")
    assert first_messages is first["messages"]
    assert second_messages is second["messages"]
    assert [item["label"] for item in captured] == ["first", "second"]
    for item, objects in zip(captured, (first, second), strict=True):
        assert item["model"] is objects["model"]
        assert item["checkpointer"] is objects["saver"]
        assert objects["tool"] in item["tools"]
        child = next(spec for spec in item["subagents"] if spec["name"] == "child")
        assert child["middleware"][0].name == "FilesystemMiddleware"
    assert config["configurable"]["thread_id"] == "facade-binding-thread"


@pytest.mark.asyncio
async def test_public_model_facade_observes_replacement_after_first_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent: Any = SimpleNamespace(
        model=SimpleNamespace(id=None, provider="openai", model_name="gpt", base_url=None),
        model_fallback_list=None,
        llm_credential_id=None,
    )
    first, second = object(), object()
    create_chat_model = model_factory.create_chat_model

    monkeypatch.setattr(model_factory, "create_chat_model", lambda *_a, **_k: first)
    assert await model_factory.create_chat_model_with_fallback(agent, MagicMock()) is first
    monkeypatch.setattr(model_factory, "create_chat_model", lambda *_a, **_k: second)
    assert await model_factory.create_chat_model_with_fallback(agent, MagicMock()) is second

    model_factory.clear_model_cache()
    first_cls, second_cls = MagicMock(name="first-cls"), MagicMock(name="second-cls")
    monkeypatch.setattr(model_factory, "ChatOpenAI", first_cls)
    assert create_chat_model("unmapped", "m1", api_key="k") is first_cls.return_value
    monkeypatch.setattr(model_factory, "ChatOpenAI", second_cls)
    assert create_chat_model("unmapped", "m2", api_key="k") is second_cls.return_value
    assert "http_client" in second_cls.call_args.kwargs
    model_factory.clear_model_cache()
