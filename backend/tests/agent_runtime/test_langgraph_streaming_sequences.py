from __future__ import annotations

import pytest

from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from tests.agent_runtime.langgraph_streaming_fixtures import ProtocolAgent, sse_payload


@pytest.mark.asyncio
async def test_streaming_assigns_unique_monotonic_seq_to_local_projection_events() -> None:
    agent = ProtocolAgent(
        [
            {
                "type": "event",
                "method": "messages",
                "params": {"namespace": [], "data": {"chunk": "hello"}},
                "seq": 1,
                "event_id": "upstream-message-1",
            },
            {
                "type": "event",
                "method": "values",
                "params": {
                    "namespace": ["tools:call-1"],
                    "data": {"__interrupt__": [{"id": "intr-1", "value": {"question": "OK?"}}]},
                },
                "seq": 1,
                "event_id": "upstream-values-1",
            },
        ]
    )

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            {"messages": []},
            {"configurable": {"thread_id": "thread-seq"}},
            run_id="run-seq",
        )
    ]

    payloads = [sse_payload(chunk) for chunk in chunks]
    assert [payload["method"] for payload in payloads] == [
        "lifecycle",
        "messages",
        "values",
        "input.requested",
        "lifecycle",
    ]
    assert [payload["seq"] for payload in payloads] == [0, 1, 2, 3, 4]
    assert payloads[3]["event_id"] == "run-seq:input:00000003:0"
