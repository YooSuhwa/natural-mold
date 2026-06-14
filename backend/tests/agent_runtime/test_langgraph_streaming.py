from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.agent_runtime.event_broker import EventBroker
from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.agent_runtime.streaming import StreamErrorRecord
from tests.agent_runtime.langgraph_streaming_fixtures import (
    ErrorAgent,
    FallbackAgent,
    ProtocolAgent,
    StateBackedProtocolAgent,
    sse_payload,
)


@pytest.mark.asyncio
async def test_langgraph_streaming_projects_usage_metadata_to_custom_event() -> None:
    raw_event = {
        "type": "event",
        "method": "messages",
        "params": {
            "namespace": [],
            "data": {
                "id": "assistant-usage-1",
                "type": "AIMessageChunk",
                "content": "",
                "usage_metadata": {
                    "input_tokens": 12,
                    "output_tokens": 5,
                    "input_token_details": {
                        "cache_creation": 2,
                        "cache_read": 3,
                    },
                },
            },
        },
        "seq": 1,
        "event_id": "usage-upstream-1",
    }
    usage_sink: dict[str, Any] = {}
    agent = ProtocolAgent([raw_event])

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            {"messages": []},
            {"configurable": {"thread_id": "thread-usage"}},
            cost_per_input_token=0.01,
            cost_per_output_token=0.02,
            usage_sink=usage_sink,
            run_id="run-usage",
        )
    ]

    payloads = [sse_payload(chunk) for chunk in chunks]
    assert [payload["method"] for payload in payloads] == [
        "lifecycle",
        "messages",
        "custom",
        "lifecycle",
    ]
    assert payloads[0]["params"]["data"] == {"event": "running"}
    assert payloads[2]["params"]["data"] == {
        "name": "usage",
        "payload": {
            "assistant_msg_id": "assistant-usage-1",
            "run_id": "run-usage",
            "prompt_tokens": 12,
            "completion_tokens": 5,
            "cache_creation_tokens": 2,
            "cache_read_tokens": 3,
            "estimated_cost": 0.22,
        },
    }
    assert payloads[3]["params"]["data"] == {"event": "completed"}
    assert usage_sink == {
        "prompt_tokens": 12,
        "completion_tokens": 5,
        "cache_creation_tokens": 2,
        "cache_read_tokens": 3,
        "estimated_cost": 0.22,
    }


@pytest.mark.asyncio
async def test_langgraph_streaming_emits_protocol_sse_and_dual_writes() -> None:
    raw_event = {
        "type": "event",
        "method": "messages",
        "params": {"namespace": [], "data": {"chunk": "hello"}},
        "seq": 1,
        "event_id": "upstream-1",
    }
    raw_interrupt_event = {
        "type": "event",
        "method": "values",
        "params": {
            "namespace": ["tools:call-1"],
            "data": {"__interrupt__": [{"id": "intr-1", "value": {"question": "approve?"}}]},
        },
        "seq": 2,
        "event_id": "upstream-2",
    }
    agent = ProtocolAgent([raw_event, raw_interrupt_event])
    broker = EventBroker("run-1")
    persisted: list[list[dict[str, Any]]] = []
    trace_sink: list[dict[str, Any]] = []

    async def persist(events: list[dict[str, Any]]) -> None:
        persisted.append(events)

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            [{"role": "user", "content": "hello"}],
            {"configurable": {"thread_id": "thread-1"}},
            broker=broker,
            persist_callback=persist,
            run_id="run-1",
            trace_sink=trace_sink,
        )
    ]

    assert agent.inputs == [{"messages": [{"role": "user", "content": "hello"}]}]
    payloads = [sse_payload(chunk) for chunk in chunks]
    assert len(chunks) == 5
    assert chunks[1].startswith("id: upstream-1\nevent: message\n")
    assert payloads[0]["method"] == "lifecycle"
    assert payloads[0]["params"]["data"] == {"event": "running"}
    assert payloads[1]["method"] == "messages"
    assert payloads[3]["method"] == "input.requested"
    assert payloads[4]["params"]["data"] == {"event": "interrupted"}
    assert persisted[0][1]["method"] == "messages"
    assert persisted[0][1]["upstream_event_id"] == "upstream-1"
    assert persisted[0][3]["method"] == "input.requested"
    assert persisted[0][3]["data"]["interrupt_id"] == "intr-1"
    assert persisted[0][4]["method"] == "lifecycle"
    assert trace_sink[3]["method"] == "input.requested"

    sequences = [payload["seq"] for payload in payloads]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))

    replayed = [event async for event in broker.subscribe()]
    assert replayed[1]["id"] == "upstream-1"
    assert replayed[0]["event"] == "message"
    assert replayed[1]["data"]["method"] == "messages"
    assert replayed[3]["data"]["method"] == "input.requested"
    assert replayed[4]["data"]["method"] == "lifecycle"


@pytest.mark.asyncio
async def test_langgraph_streaming_persists_compact_values_snapshots() -> None:
    raw_event = {
        "type": "event",
        "method": "values",
        "params": {
            "namespace": [],
            "data": {
                "messages": [
                    {
                        "id": "msg-1",
                        "type": "ai",
                        "content": "large assistant text that belongs in the checkpoint",
                        "additional_kwargs": {"reasoning": "private chain"},
                    }
                ],
                "todos": [{"id": "todo-1", "content": "ship v3", "status": "in_progress"}],
            },
        },
        "seq": 1,
        "event_id": "values-1",
    }
    agent = ProtocolAgent([raw_event])
    persisted: list[list[dict[str, Any]]] = []

    async def persist(events: list[dict[str, Any]]) -> None:
        persisted.append(events)

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            {"messages": []},
            {"configurable": {"thread_id": "thread-values"}},
            persist_callback=persist,
            run_id="run-values",
        )
    ]

    payloads = [sse_payload(chunk) for chunk in chunks]
    live_values = [payload for payload in payloads if payload["method"] == "values"][0]
    assert live_values["params"]["data"]["messages"][0]["content"] == (
        "large assistant text that belongs in the checkpoint"
    )

    persisted_values = [event for event in persisted[0] if event["method"] == "values"][0]
    assert persisted_values["data"] == {
        "messages": [{"id": "msg-1", "type": "ai"}],
        "todos": [{"id": "todo-1", "content": "ship v3", "status": "in_progress"}],
    }


@pytest.mark.asyncio
async def test_langgraph_streaming_emits_state_pending_interrupt_before_persist_and_close() -> None:
    raw_event = {
        "type": "event",
        "method": "messages",
        "params": {"namespace": [], "data": {"chunk": "waiting"}},
        "seq": 1,
        "event_id": "upstream-waiting",
    }
    long_interrupt_id = "intr-" + ("x" * 120)
    state = SimpleNamespace(
        interrupts=[
            SimpleNamespace(
                id=long_interrupt_id,
                value={
                    "action_requests": [
                        {"name": "execute_in_skill", "args": {"command": "make-docx"}}
                    ],
                    "review_configs": [
                        {
                            "action_name": "execute_in_skill",
                            "allowed_decisions": ["approve", "reject"],
                        }
                    ],
                },
            )
        ]
    )
    agent = StateBackedProtocolAgent([raw_event], state)
    broker = EventBroker("123e4567-e89b-12d3-a456-426614174000")
    persisted: list[list[dict[str, Any]]] = []
    trace_sink: list[dict[str, Any]] = []

    async def persist(events: list[dict[str, Any]]) -> None:
        persisted.append(events)

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            {"messages": []},
            {"configurable": {"thread_id": "thread-state-hitl"}},
            broker=broker,
            persist_callback=persist,
            run_id="123e4567-e89b-12d3-a456-426614174000",
            trace_sink=trace_sink,
        )
    ]

    payloads = [sse_payload(chunk) for chunk in chunks]
    input_requested = [payload for payload in payloads if payload["method"] == "input.requested"]
    assert len(input_requested) == 1
    event_id = input_requested[0]["event_id"]
    assert event_id == "123e4567-e89b-12d3-a456-426614174000:input:00000002:0"
    assert len(event_id) <= 80
    assert input_requested[0]["params"]["data"]["interrupt_id"] == long_interrupt_id

    assert persisted[0][-2]["method"] == "input.requested"
    assert persisted[0][-2]["upstream_event_id"] == event_id
    assert persisted[0][-1]["method"] == "lifecycle"
    assert persisted[0][-1]["data"] == {"event": "interrupted"}
    assert trace_sink[-2]["method"] == "input.requested"
    assert trace_sink[-1]["data"] == {"event": "interrupted"}

    replayed = [event async for event in broker.subscribe()]
    assert replayed[-2]["id"] == event_id
    assert replayed[-2]["data"]["method"] == "input.requested"
    assert replayed[-1]["data"]["method"] == "lifecycle"
    assert broker.last_event_id == replayed[-1]["id"]


@pytest.mark.asyncio
async def test_langgraph_streaming_falls_back_to_stream_modes() -> None:
    agent = FallbackAgent([("values", {"messages": [], "todos": []})])

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            {"messages": []},
            {"configurable": {"thread_id": "thread-2"}},
            run_id="run-2",
        )
    ]

    payloads = [sse_payload(chunk) for chunk in chunks]
    assert [payload["method"] for payload in payloads] == ["lifecycle", "values", "lifecycle"]
    payload = payloads[1]
    assert payload["method"] == "values"
    assert payload["params"]["data"] == {"messages": [], "todos": []}


@pytest.mark.asyncio
async def test_langgraph_streaming_reports_stream_errors() -> None:
    errors: list[StreamErrorRecord] = []

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            ErrorAgent(),
            None,
            {"configurable": {"thread_id": "thread-3"}},
            error_sink=errors,
            run_id="run-3",
        )
    ]

    payloads = [sse_payload(chunk) for chunk in chunks]
    assert [payload["method"] for payload in payloads] == ["lifecycle", "lifecycle", "error"]
    assert payloads[1]["params"]["data"] == {
        "event": "failed",
        "error": {"message": "stream failed"},
    }
    assert payloads[2]["params"]["data"] == {"message": "stream failed"}
    assert errors[0].message == "stream failed"
