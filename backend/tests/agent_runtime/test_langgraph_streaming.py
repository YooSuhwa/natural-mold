from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent_runtime.event_broker import EventBroker
from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.agent_runtime.streaming import StreamErrorRecord


class ProtocolAgent:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.inputs: list[Any] = []

    async def astream_events(self, input_: Any, **_kwargs: Any) -> Any:
        self.inputs.append(input_)

        async def _stream() -> Any:
            for event in self.events:
                yield event

        return _stream()


class StateBackedProtocolAgent(ProtocolAgent):
    def __init__(self, events: list[dict[str, Any]], state: Any) -> None:
        super().__init__(events)
        self.state = state

    async def aget_state(self, _config: dict[str, Any]) -> Any:
        return self.state


class FallbackAgent:
    def __init__(self, chunks: list[tuple[str, Any]]) -> None:
        self.chunks = chunks

    async def astream_events(self, *_args: Any, **_kwargs: Any) -> Any:
        raise NotImplementedError("v3 unavailable")

    async def astream(self, *_args: Any, **_kwargs: Any) -> Any:
        for chunk in self.chunks:
            yield chunk


class ErrorAgent:
    async def astream_events(self, *_args: Any, **_kwargs: Any) -> Any:
        async def _stream() -> Any:
            raise RuntimeError("stream failed")
            yield {}

        return _stream()


class FakeArtifactRecorder:
    def __init__(self) -> None:
        self.prepared = False
        self.calls: list[tuple[str, str | None]] = []

    async def prepare(self) -> None:
        self.prepared = True

    async def collect_after_tool_result(
        self,
        *,
        tool_name: str,
        tool_call_id: str | None,
    ) -> list[dict[str, object]]:
        self.calls.append((tool_name, tool_call_id))
        return [{"op": "created", "id": "artifact-1", "path": "report.md"}]


def _sse_payload(raw: str) -> dict[str, Any]:
    data_line = next(line for line in raw.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert isinstance(payload, dict)
    return payload


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

    payloads = [_sse_payload(chunk) for chunk in chunks]
    assert [payload["method"] for payload in payloads] == ["messages", "custom:usage"]
    assert payloads[1]["params"]["data"] == {
        "assistant_msg_id": "assistant-usage-1",
        "run_id": "run-usage",
        "prompt_tokens": 12,
        "completion_tokens": 5,
        "cache_creation_tokens": 2,
        "cache_read_tokens": 3,
        "estimated_cost": 0.22,
    }
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
    assert len(chunks) == 3
    assert chunks[0].startswith("id: upstream-1\nevent: message\n")
    assert _sse_payload(chunks[0])["method"] == "messages"
    assert _sse_payload(chunks[2])["method"] == "input.requested"
    assert persisted[0][0]["method"] == "messages"
    assert persisted[0][0]["upstream_event_id"] == "upstream-1"
    assert persisted[0][2]["method"] == "input.requested"
    assert persisted[0][2]["data"]["interrupt_id"] == "intr-1"
    assert trace_sink[2]["method"] == "input.requested"

    replayed = [event async for event in broker.subscribe()]
    assert replayed[0]["id"] == "upstream-1"
    assert replayed[0]["event"] == "message"
    assert replayed[0]["data"]["method"] == "messages"
    assert replayed[2]["data"]["method"] == "input.requested"


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

    payloads = [_sse_payload(chunk) for chunk in chunks]
    input_requested = [payload for payload in payloads if payload["method"] == "input.requested"]
    assert len(input_requested) == 1
    event_id = input_requested[0]["event_id"]
    assert event_id == "123e4567-e89b-12d3-a456-426614174000:input:00000002:0"
    assert len(event_id) <= 80
    assert input_requested[0]["params"]["data"]["interrupt_id"] == long_interrupt_id

    assert persisted[0][-1]["method"] == "input.requested"
    assert persisted[0][-1]["upstream_event_id"] == event_id
    assert trace_sink[-1]["method"] == "input.requested"

    replayed = [event async for event in broker.subscribe()]
    assert replayed[-1]["id"] == event_id
    assert replayed[-1]["data"]["method"] == "input.requested"
    assert broker.last_event_id == event_id


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

    payload = _sse_payload(chunks[0])
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

    payload = _sse_payload(chunks[0])
    assert payload["method"] == "error"
    assert payload["params"]["data"] == {"message": "stream failed"}
    assert errors[0].message == "stream failed"


@pytest.mark.asyncio
async def test_langgraph_streaming_emits_file_event_after_artifact_tool_result() -> None:
    raw_event = {
        "type": "event",
        "method": "tools",
        "params": {
            "namespace": [],
            "data": {
                "event": "tool-finished",
                "tool_name": "execute_in_skill",
                "tool_call_id": "call-1",
                "output": "done",
            },
        },
        "seq": 1,
        "event_id": "tool-finished-1",
    }
    recorder = FakeArtifactRecorder()
    agent = ProtocolAgent([raw_event])

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            {"messages": []},
            {"configurable": {"thread_id": "thread-artifacts"}},
            artifact_recorder=recorder,
            run_id="run-artifacts",
        )
    ]

    payloads = [_sse_payload(chunk) for chunk in chunks]
    assert recorder.prepared is True
    assert recorder.calls == [("execute_in_skill", "call-1")]
    assert [payload["method"] for payload in payloads] == ["tools", "custom:file_event"]
    assert payloads[1]["params"]["data"] == {
        "op": "created",
        "id": "artifact-1",
        "path": "report.md",
    }


@pytest.mark.asyncio
async def test_langgraph_streaming_emits_memory_event_after_memory_tool_result() -> None:
    raw_event = {
        "type": "event",
        "method": "tools",
        "params": {
            "namespace": [],
            "data": {
                "event": "tool-finished",
                "tool_name": "save_user_memory",
                "tool_call_id": "memory-call-1",
                "output": json.dumps(
                    {
                        "memory_event": "memory_saved",
                        "id": "memory-1",
                        "scope": "user",
                        "content": "User prefers concise answers.",
                        "reason": None,
                        "policy": "auto",
                    }
                ),
            },
        },
        "seq": 1,
        "event_id": "memory-tool-finished-1",
    }
    agent = ProtocolAgent([raw_event])

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            {"messages": []},
            {"configurable": {"thread_id": "thread-memory"}},
            run_id="run-memory",
        )
    ]

    payloads = [_sse_payload(chunk) for chunk in chunks]
    assert [payload["method"] for payload in payloads] == ["tools", "custom:memory_saved"]
    assert payloads[1]["params"]["data"] == {
        "id": "memory-1",
        "scope": "user",
        "content": "User prefers concise answers.",
        "reason": None,
        "policy": "auto",
    }
