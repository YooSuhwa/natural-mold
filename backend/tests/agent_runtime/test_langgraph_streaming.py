from __future__ import annotations

import json
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


def _sse_payload(raw: str) -> dict[str, Any]:
    data_line = next(line for line in raw.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert isinstance(payload, dict)
    return payload


@pytest.mark.asyncio
async def test_langgraph_streaming_emits_protocol_sse_and_dual_writes() -> None:
    raw_event = {
        "type": "event",
        "method": "messages",
        "params": {"namespace": [], "data": {"chunk": "hello"}},
        "seq": 1,
        "event_id": "upstream-1",
    }
    agent = ProtocolAgent([raw_event])
    broker = EventBroker("run-1")
    persisted: list[list[dict[str, Any]]] = []

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
        )
    ]

    assert agent.inputs == [{"messages": [{"role": "user", "content": "hello"}]}]
    assert len(chunks) == 1
    assert chunks[0].startswith("id: upstream-1\nevent: message\n")
    assert _sse_payload(chunks[0])["method"] == "messages"
    assert persisted[0][0]["method"] == "messages"
    assert persisted[0][0]["upstream_event_id"] == "upstream-1"

    replayed = [event async for event in broker.subscribe()]
    assert replayed[0]["id"] == "upstream-1"
    assert replayed[0]["event"] == "message"
    assert replayed[0]["data"]["method"] == "messages"


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
