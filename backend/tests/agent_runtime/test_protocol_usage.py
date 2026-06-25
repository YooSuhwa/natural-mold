from __future__ import annotations

from app.agent_runtime.langgraph_protocol_adapter import adapt_v3_protocol_event
from app.agent_runtime.protocol_events import stored_protocol_event
from app.agent_runtime.protocol_usage import collect_protocol_usage_event


def test_usage_reachable_when_living_in_messages_stream_metadata() -> None:
    # The v3 messages adapter flattens the SDK ``[payload, metadata]`` tuple into a
    # single mapping, nesting the stream metadata (which carries usage) under
    # ``payload["metadata"]``. Usage must remain reachable through that nesting.
    event = adapt_v3_protocol_event(
        {
            "type": "event",
            "method": "messages",
            "params": {
                "data": (
                    {"id": "assistant-1", "content": "hi"},
                    {
                        "langgraph_node": "model",
                        "usage_metadata": {
                            "input_tokens": 30,
                            "output_tokens": 12,
                        },
                    },
                ),
            },
            "seq": 4,
        },
        run_id="run-1",
        thread_id="thread-1",
    )

    assert event["data"]["metadata"]["usage_metadata"] == {
        "input_tokens": 30,
        "output_tokens": 12,
    }

    usage_event, seq = collect_protocol_usage_event(
        event,
        next_seq=event["seq"],
        seen_keys=set(),
        usage_sink=None,
        cost_per_input_token=None,
        cost_per_output_token=None,
    )

    assert usage_event is not None
    assert usage_event["data"]["name"] == "usage"
    payload = usage_event["data"]["payload"]
    assert payload["prompt_tokens"] == 30
    assert payload["completion_tokens"] == 12
    assert seq > event["seq"]


def test_usage_not_emitted_when_metadata_has_no_usage() -> None:
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=2,
        method="messages",
        data={"id": "assistant-1", "content": "hi", "metadata": {"langgraph_node": "model"}},
    )

    usage_event, seq = collect_protocol_usage_event(
        event,
        next_seq=event["seq"],
        seen_keys=set(),
        usage_sink=None,
        cost_per_input_token=None,
        cost_per_output_token=None,
    )

    assert usage_event is None
    assert seq == event["seq"]
