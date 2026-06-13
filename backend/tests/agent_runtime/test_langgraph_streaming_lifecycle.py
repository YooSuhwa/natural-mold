from __future__ import annotations

from typing import Any

import pytest

from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from tests.agent_runtime.langgraph_streaming_fixtures import ProtocolAgent, sse_payload


@pytest.mark.asyncio
async def test_langgraph_streaming_emits_root_lifecycle_around_completed_run() -> None:
    raw_event = {
        "type": "event",
        "method": "messages",
        "params": {"namespace": [], "data": {"chunk": "done"}},
        "seq": 1,
        "event_id": "message-1",
    }
    trace_sink: list[dict[str, Any]] = []

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            ProtocolAgent([raw_event]),
            {"messages": []},
            {"configurable": {"thread_id": "thread-complete"}},
            run_id="run-complete",
            trace_sink=trace_sink,
        )
    ]

    payloads = [sse_payload(chunk) for chunk in chunks]
    assert [payload["method"] for payload in payloads] == [
        "lifecycle",
        "messages",
        "lifecycle",
    ]
    assert payloads[0]["params"]["namespace"] == []
    assert payloads[0]["params"]["data"] == {"event": "running"}
    assert payloads[-1]["params"]["data"] == {"event": "completed"}
    assert payloads[-1]["seq"] > payloads[1]["seq"]
    assert trace_sink[0]["method"] == "lifecycle"
    assert trace_sink[-1]["method"] == "lifecycle"


@pytest.mark.asyncio
async def test_langgraph_streaming_marks_run_interrupted_when_input_is_pending() -> None:
    raw_event = {
        "type": "event",
        "method": "values",
        "params": {
            "namespace": [],
            "data": {"__interrupt__": [{"id": "intr-1", "value": {"question": "approve?"}}]},
        },
        "seq": 1,
        "event_id": "interrupt-1",
    }

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            ProtocolAgent([raw_event]),
            {"messages": []},
            {"configurable": {"thread_id": "thread-interrupt"}},
            run_id="run-interrupt",
        )
    ]

    payloads = [sse_payload(chunk) for chunk in chunks]
    assert [payload["method"] for payload in payloads] == [
        "lifecycle",
        "values",
        "input.requested",
        "lifecycle",
    ]
    assert payloads[-1]["params"]["data"] == {"event": "interrupted"}


@pytest.mark.asyncio
async def test_langgraph_streaming_lifecycle_ids_are_stable_sse_cursors() -> None:
    raw_event = {
        "type": "event",
        "method": "messages",
        "params": {"namespace": [], "data": {"chunk": "done"}},
        "seq": 1,
        "event_id": "message-1",
    }

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            ProtocolAgent([raw_event]),
            {"messages": []},
            {"configurable": {"thread_id": "thread-cursors"}},
            run_id="run-cursors",
        )
    ]

    payloads = [sse_payload(chunk) for chunk in chunks]
    assert chunks[0].startswith("id: run-cursors:lifecycle:running\n")
    assert chunks[-1].startswith("id: run-cursors:lifecycle:completed\n")
    assert payloads[0]["event_id"] == "run-cursors:lifecycle:running"
    assert payloads[-1]["event_id"] == "run-cursors:lifecycle:completed"
