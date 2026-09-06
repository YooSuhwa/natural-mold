"""Compile Assistant and parent/child profiles through the real agent facade."""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from deepagents.backends import StateBackend
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.tools import BaseTool, StructuredTool

from app.agent_runtime import runtime_component_builder as builder
from tests.agent_runtime.runtime_contract_helpers import (
    JSONValue,
    contract_diff_paths,
    load_contract_fixture,
)

_FIXTURE = "runtime_profiles_v1.json"


def _tool_result() -> str:
    return "profile-result"


def _tool(name: str) -> BaseTool:
    return StructuredTool.from_function(
        _tool_result,
        name=name,
        description=f"Hermetic {name} profile tool.",
    )


def _compiled_shape(graph: Any) -> dict[str, JSONValue]:
    tool_node = graph.nodes["tools"].bound
    tools_by_name = getattr(tool_node, "_tools_by_name", {})
    return {
        "nodes": sorted(graph.nodes),
        "tools": sorted(tools_by_name),
        "checkpointer_attached": graph.checkpointer is not None,
    }


async def _assistant_manifest(monkeypatch: pytest.MonkeyPatch) -> dict[str, JSONValue]:
    from app.agent_runtime.assistant import assistant_agent

    model = FakeListChatModel(responses=["assistant-response"])
    monkeypatch.setattr(
        assistant_agent,
        "resolve_system_model",
        AsyncMock(
            return_value=SimpleNamespace(
                provider="fake",
                model_name="assistant-model",
                api_key=None,
                base_url=None,
            )
        ),
    )
    monkeypatch.setattr(assistant_agent, "create_chat_model", lambda *_args, **_kwargs: model)
    monkeypatch.setattr(
        assistant_agent, "build_read_tools", lambda *_args: [_tool("assistant_read")]
    )
    monkeypatch.setattr(
        assistant_agent, "build_write_tools", lambda *_args: [_tool("assistant_write")]
    )
    monkeypatch.setattr(
        assistant_agent, "build_clarify_tools", lambda: [_tool("assistant_clarify")]
    )
    monkeypatch.setattr(assistant_agent, "get_checkpointer", lambda: None)
    monkeypatch.setattr(assistant_agent, "_load_system_prompt", lambda: "<assistant-profile>")

    graph = await assistant_agent.build_assistant_agent(
        AsyncMock(),
        UUID("00000000-0000-4000-8000-000000000001"),
        UUID("00000000-0000-4000-8000-000000000002"),
        "assistant-thread",
    )
    return _compiled_shape(graph)


def _subagent_shape(spec: Mapping[str, Any]) -> dict[str, JSONValue]:
    return {
        "name": str(spec["name"]),
        "tools": [str(tool.name) for tool in spec.get("tools", [])],
        "has_nested_subagents": "subagents" in spec,
        "interrupt_inherited": spec.get("interrupt_on")
        == {"parent_tool": {"allowed_decisions": ["approve"]}},
    }


def _parent_child_manifest() -> dict[str, JSONValue]:
    model = FakeListChatModel(responses=["parent-response"])
    parent_tool = _tool("parent_tool")
    child_tool = _tool("child_tool")
    interrupt_on = {"parent_tool": {"allowed_decisions": ["approve"]}}
    child = {
        "name": "child_runtime",
        "description": "Hermetic child runtime.",
        "system_prompt": "<child-profile>",
        "tools": [child_tool],
    }
    backend = StateBackend()
    normalized = builder._normalize_declarative_subagents(
        [child],
        model=model,
        tools=[parent_tool],
        backend=backend,
        permissions=None,
        interrupt_on=interrupt_on,
        skills=None,
    )
    graph = builder.build_agent(
        model,
        [parent_tool],
        "<parent-profile>",
        backend=backend,
        interrupt_on=interrupt_on,
        subagents=[child],
    )
    return {
        "compiled": _compiled_shape(graph),
        "subagents": [_subagent_shape(spec) for spec in normalized],
    }


def _assert_graph_contract(actual: dict[str, JSONValue]) -> None:
    fixture = load_contract_fixture(_FIXTURE)
    expected = {name: fixture[name] for name in actual}
    differences = contract_diff_paths(expected, actual)
    assert not differences, f"contract drift at: {', '.join(differences[:20])}"


@pytest.mark.asyncio
async def test_assistant_and_parent_child_compile_to_reviewed_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_graph_contract(
        {
            "assistant": await _assistant_manifest(monkeypatch),
            "parent_child": _parent_child_manifest(),
        }
    )
