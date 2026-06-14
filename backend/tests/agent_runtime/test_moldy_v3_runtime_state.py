from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from app.agent_runtime.runtime_component_builder import _prepare_agent
from app.agent_runtime.runtime_config import AgentConfig

pytestmark = pytest.mark.filterwarnings(
    "ignore:The v3 streaming protocol on Pregel is experimental"
)


class FakeToolBindingChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeToolBindingChatModel:
        return self


async def _collect_moldy_v3_events(
    monkeypatch: pytest.MonkeyPatch,
    *,
    model: FakeToolBindingChatModel,
    cfg: AgentConfig,
) -> list[dict[str, Any]]:
    monkeypatch.setattr(
        "app.agent_runtime.runtime_component_builder._build_model_candidates",
        lambda _cfg: [model],
    )
    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", MemorySaver)

    agent, lc_messages, config = await _prepare_agent(
        cfg,
        messages_history=[{"role": "user", "content": "research v3"}],
    )
    stream = await agent.astream_events({"messages": lc_messages}, config=config, version="v3")

    events: list[dict[str, Any]] = []
    async for event in stream:
        events.append(event)
    return events


def _event_methods(events: list[dict[str, Any]]) -> set[str]:
    return {method for event in events if isinstance(method := event.get("method"), str)}


def _event_params(event: dict[str, Any]) -> dict[str, Any]:
    params = event.get("params")
    return params if isinstance(params, dict) else {}


@pytest.mark.asyncio
async def test_moldy_runtime_v3_stream_preserves_subagent_namespace_and_files_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subagent_model = FakeToolBindingChatModel(
        responses=[AIMessage(content="Subagent answer.", id="sub-1")]
    )
    supervisor_model = FakeToolBindingChatModel(
        responses=[
            AIMessage(
                content="",
                id="sup-1",
                tool_calls=[
                    {
                        "id": "tc-task-1",
                        "name": "task",
                        "args": {
                            "subagent_type": "researcher",
                            "description": "research v3",
                        },
                    }
                ],
            ),
            AIMessage(content="Research complete.", id="sup-2"),
        ]
    )
    cfg = AgentConfig(
        provider="fake",
        model_name="fake-chat",
        api_key=None,
        base_url=None,
        system_prompt="Use researcher when research is requested.",
        tools_config=[],
        thread_id="moldy-v3-subagent-thread",
        agent_runtime_name="moldy-v3-agent",
        subagents_config=[
            {
                "name": "researcher",
                "description": "Researches topics.",
                "system_prompt": "Answer briefly.",
                "model": subagent_model,
            }
        ],
    )

    events = await _collect_moldy_v3_events(monkeypatch, model=supervisor_model, cfg=cfg)

    assert {"values", "messages", "tools", "lifecycle"} <= _event_methods(events)
    value_payloads = [
        _event_params(event).get("data") for event in events if event.get("method") == "values"
    ]
    assert any(
        isinstance(payload, dict)
        and isinstance(payload.get("files"), dict)
        and "messages" in payload
        for payload in value_payloads
    )

    lifecycle_payloads = [
        _event_params(event).get("data") for event in events if event.get("method") == "lifecycle"
    ]
    assert any(
        isinstance(payload, dict)
        and payload.get("event") == "started"
        and payload.get("graph_name") == "researcher"
        and isinstance(payload.get("namespace"), list)
        and payload["namespace"][0].startswith("tools:")
        and payload.get("cause") == {"type": "toolCall", "tool_call_id": "tc-task-1"}
        for payload in lifecycle_payloads
    )

    assert any(_event_params(event).get("namespace") for event in events)
    message_payloads = [
        _event_params(event).get("data") for event in events if event.get("method") == "messages"
    ]
    assert any(
        isinstance(payload, tuple)
        and len(payload) == 2
        and isinstance(payload[1], dict)
        and isinstance(payload[1].get("checkpoint_ns"), str)
        for payload in message_payloads
    )
    assert any("timestamp" in _event_params(event) for event in events)


@pytest.mark.asyncio
async def test_moldy_runtime_v3_stream_exposes_todos_when_write_todos_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeToolBindingChatModel(
        responses=[
            AIMessage(
                content="",
                id="todo-1",
                tool_calls=[
                    {
                        "id": "tc-todos-1",
                        "name": "write_todos",
                        "args": {"todos": [{"content": "Plan work", "status": "in_progress"}]},
                    }
                ],
            ),
            AIMessage(content="Plan recorded.", id="todo-2"),
        ]
    )
    cfg = AgentConfig(
        provider="fake",
        model_name="fake-chat",
        api_key=None,
        base_url=None,
        system_prompt="Plan before answering.",
        tools_config=[],
        thread_id="moldy-v3-todos-thread",
        agent_runtime_name="moldy-v3-todos-agent",
    )

    events = await _collect_moldy_v3_events(monkeypatch, model=model, cfg=cfg)

    value_payloads = [
        _event_params(event).get("data") for event in events if event.get("method") == "values"
    ]
    assert any(
        isinstance(payload, dict)
        and payload.get("todos") == [{"content": "Plan work", "status": "in_progress"}]
        for payload in value_payloads
    )
    assert any(
        isinstance((data := _event_params(event).get("data")), dict)
        and data.get("event") == "tool-started"
        and data.get("tool_name") == "write_todos"
        for event in events
        if event.get("method") == "tools"
    )
