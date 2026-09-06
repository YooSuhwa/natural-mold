from __future__ import annotations

import pytest

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


@pytest.mark.parametrize(
    ("direct_usage", "expected_prompt", "expected_completion"),
    [
        (None, 17, 6),
        ({}, 17, 6),
        ({"input_tokens": 0, "output_tokens": 0}, 17, 6),
        ({"input_tokens": 2, "output_tokens": 1}, 2, 1),
    ],
)
def test_typed_ai_payload_uses_usable_direct_usage_then_sibling_metadata(
    direct_usage: dict[str, int] | None,
    expected_prompt: int,
    expected_completion: int,
) -> None:
    payload: dict[str, object] = {
        "id": "typed-assistant",
        "type": "AIMessageChunk",
        "content": "typed",
    }
    if direct_usage is not None:
        payload["usage_metadata"] = direct_usage
    event = adapt_v3_protocol_event(
        {
            "type": "event",
            "method": "messages",
            "params": {
                "data": (
                    payload,
                    {
                        "langgraph_node": "model",
                        "usage_metadata": {"input_tokens": 17, "output_tokens": 6},
                    },
                ),
            },
            "seq": 5,
        },
        run_id="run-1",
        thread_id="thread-1",
    )

    usage_event, _seq = collect_protocol_usage_event(
        event,
        next_seq=event["seq"],
        seen_keys=set(),
        usage_sink=None,
        cost_per_input_token=None,
        cost_per_output_token=None,
    )

    assert usage_event is not None
    assert usage_event["data"]["payload"]["assistant_msg_id"] == "typed-assistant"
    assert usage_event["data"]["payload"]["prompt_tokens"] == expected_prompt
    assert usage_event["data"]["payload"]["completion_tokens"] == expected_completion


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


def test_usage_sink_receives_same_timing_snapshot_as_emitted_event() -> None:
    # Given: provider usage arrives after a known first-token boundary.
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=3,
        method="messages",
        data={
            "id": "assistant-1",
            "content": "done",
            "usage_metadata": {"input_tokens": 5, "output_tokens": 2},
        },
    )
    usage_sink: dict[str, object] = {}

    # When: usage is projected to its custom protocol event.
    usage_event, _seq = collect_protocol_usage_event(
        event,
        next_seq=event["seq"],
        seen_keys=set(),
        usage_sink=usage_sink,
        cost_per_input_token=None,
        cost_per_output_token=None,
        started_at=10.0,
        first_token_at=11.0,
    )

    # Then: the incremental sink and emitted payload share one completed snapshot.
    assert usage_event is not None
    payload = usage_event["data"]["payload"]
    assert usage_sink["ttft_ms"] == payload["ttft_ms"]
    assert usage_sink["generation_ms"] == payload["generation_ms"]
    assert usage_sink["tokens_per_second"] == payload["tokens_per_second"]
