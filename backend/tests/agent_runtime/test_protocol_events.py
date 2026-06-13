from __future__ import annotations

import json

import pytest

from app.agent_runtime.protocol_events import (
    format_protocol_sse,
    matches_subscription,
    stored_protocol_event,
    to_assistant_ui_projection,
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
    )

    wire = to_protocol_wire_event(event)

    assert wire["method"] == "messages"
    assert wire["params"]["namespace"] == ["tools:tc-1"]
    assert wire["params"]["data"] == [
        {"type": "AIMessageChunk", "content": "hi"},
        {"langgraph_node": "model"},
    ]
    assert "timestamp" not in wire["params"]
    assert wire["seq"] == 1
    assert wire["event_id"] == "evt-1"


def test_optional_assistant_ui_projection_uses_pipe_suffix() -> None:
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=1,
        event_id="evt-1",
        method="messages",
        namespace=["tools:tc-1", "child/1"],
        data={"content": "hi"},
    )

    assert to_assistant_ui_projection(event) == {
        "event": "messages|tools%3Atc-1|child%2F1",
        "data": {"content": "hi"},
    }


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


def test_rejects_non_json_data() -> None:
    with pytest.raises(TypeError, match="JSON serializable"):
        stored_protocol_event(
            run_id="run-1",
            thread_id="thread-1",
            seq=1,
            method="values",
            data={"bad": {object()}},
        )
