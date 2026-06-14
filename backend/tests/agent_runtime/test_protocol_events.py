from __future__ import annotations

import json

import pytest

from app.agent_runtime.protocol_events import (
    canonical_input_requested_events,
    format_protocol_sse,
    matches_subscription,
    protocol_interrupts_from_event,
    stored_protocol_event,
    to_protocol_wire_event,
)


def test_stored_event_yields_protocol_shape() -> None:
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=1,
        event_id="evt-1",
        method="messages",
        namespace=["tools:tc-1"],
        data=[{"type": "AIMessageChunk", "content": "hi"}, {"langgraph_node": "model"}],
        timestamp=None,
        checkpoint_id="ck-1",
        checkpoint_ns="model",
    )

    wire = to_protocol_wire_event(event)

    assert wire["method"] == "messages"
    assert wire["params"]["namespace"] == ["tools:tc-1"]
    assert wire["params"]["checkpoint_id"] == "ck-1"
    assert wire["params"]["checkpoint_ns"] == "model"
    assert wire["params"]["data"] == [
        {"type": "AIMessageChunk", "content": "hi"},
        {"langgraph_node": "model"},
    ]
    assert "timestamp" not in wire["params"]
    assert wire["seq"] == 1
    assert wire["event_id"] == "evt-1"


def test_format_protocol_sse_uses_protocol_message_event() -> None:
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=7,
        event_id="evt-7",
        method="values",
        data={"todos": []},
        timestamp="2026-06-13T00:00:00+00:00",
    )

    rendered = format_protocol_sse(event)

    assert rendered.startswith("id: evt-7\nevent: message\ndata: ")
    payload = json.loads(rendered.split("data: ", 1)[1])
    assert payload == {
        "type": "event",
        "method": "values",
        "params": {
            "namespace": [],
            "data": {"todos": []},
            "timestamp": "2026-06-13T00:00:00+00:00",
        },
        "seq": 7,
        "event_id": "evt-7",
    }


def test_format_protocol_sse_includes_auto_event_id_when_upstream_id_is_absent() -> None:
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=3,
        method="custom",
        data={"payload": "fallback"},
    )

    rendered = format_protocol_sse(event)

    payload = json.loads(rendered.split("data: ", 1)[1])
    assert payload["event_id"] == "run-1:protocol:00000003"


def test_format_protocol_sse_stringifies_integers_outside_orjson_range() -> None:
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=4,
        method="custom",
        data={"value": 2**80},
    )

    rendered = format_protocol_sse(event)

    payload = json.loads(rendered.split("data: ", 1)[1])
    assert payload["params"]["data"]["value"] == str(2**80)


def test_matches_subscription_filters_channel_namespace_depth_and_since() -> None:
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=10,
        event_id="evt-10",
        method="messages",
        namespace=["agent", "researcher"],
        data={"content": "nested"},
    )

    assert matches_subscription(
        event,
        {
            "channels": ["messages"],
            "namespaces": [["agent"]],
            "depth": 1,
            "since": 9,
        },
    )
    assert not matches_subscription(event, {"channels": ["tools"]})
    assert not matches_subscription(event, {"namespaces": [["agent"]], "depth": 0})
    assert not matches_subscription(event, {"since": "evt-10"})


def test_matches_custom_named_channel() -> None:
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=3,
        method="custom",
        data={"name": "artifact", "path": "report.md"},
    )

    assert matches_subscription(event, {"channels": ["custom"]})
    assert matches_subscription(event, {"channels": ["custom:artifact"]})
    assert not matches_subscription(event, {"channels": ["custom:memory"]})


def test_matches_input_requested_when_subscribed_to_input_channel() -> None:
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=3,
        method="input.requested",
        data={"interrupt_id": "intr-1", "payload": {"question": "approve?"}},
    )

    assert matches_subscription(event, {"channels": ["input"]})


def test_canonicalizes_values_interrupt_to_input_requested() -> None:
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=6,
        event_id="evt-values-6",
        method="values",
        namespace=["tools:call-1"],
        data={
            "__interrupt__": [
                {
                    "id": "intr-1",
                    "value": {"question": "approve execute_in_skill?"},
                }
            ]
        },
    )

    [canonical] = canonical_input_requested_events(event)

    assert canonical["method"] == "input.requested"
    assert canonical["upstream_event_id"] == "run-1:input:00000006:0"
    assert canonical["seq"] == 6
    assert canonical["namespace"] == ["tools:call-1"]
    assert canonical["data"] == {
        "interrupt_id": "intr-1",
        "payload": {"question": "approve execute_in_skill?"},
        "namespace": ["tools:call-1"],
    }


def test_canonical_input_requested_event_ids_are_db_safe_and_unique() -> None:
    event = stored_protocol_event(
        run_id="123e4567-e89b-12d3-a456-426614174000",
        thread_id="thread-1",
        seq=10,
        event_id=(
            "123e4567-e89b-12d3-a456-426614174000:protocol:00000010:"
            "input-requested:very-long-interrupt-id-that-would-overflow"
        ),
        method="values",
        data={
            "__interrupt__": [
                {"id": "intr-1", "value": {"question": "first?"}},
                {"id": "intr-2", "value": {"question": "second?"}},
            ]
        },
    )

    canonicals = canonical_input_requested_events(event)

    sequences = [canonical["seq"] for canonical in canonicals]
    event_ids = [canonical["upstream_event_id"] for canonical in canonicals]
    assert sequences == [10, 11]
    assert event_ids == [
        "123e4567-e89b-12d3-a456-426614174000:input:00000010:0",
        "123e4567-e89b-12d3-a456-426614174000:input:00000011:1",
    ]
    assert all(event_id is not None and len(event_id) <= 80 for event_id in event_ids)


def test_extracts_tasks_interrupts() -> None:
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=8,
        method="tasks",
        namespace=["agent"],
        data={
            "id": "task-1",
            "name": "tools",
            "interrupts": [
                {"id": "intr-2", "value": {"action_requests": []}, "ns": ["tools:call-2"]}
            ],
        },
    )

    assert protocol_interrupts_from_event(event) == [
        {"id": "intr-2", "value": {"action_requests": []}, "ns": ["tools:call-2"]}
    ]


def test_rejects_non_json_data() -> None:
    with pytest.raises(TypeError, match="JSON serializable"):
        stored_protocol_event(
            run_id="run-1",
            thread_id="thread-1",
            seq=1,
            method="values",
            data={"bad": {object()}},
        )
