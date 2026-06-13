from __future__ import annotations

from dataclasses import dataclass

from app.agent_runtime.langgraph_protocol_adapter import (
    adapt_stream_mode_chunk,
    adapt_v3_protocol_event,
    extract_subagent_discovery,
    synthesize_tool_events_from_values,
)
from app.agent_runtime.protocol_events import to_assistant_ui_projection


@dataclass
class FakeChunk:
    id: str
    content: str
    type: str = "AIMessageChunk"
    tool_call_chunks: list[dict[str, object]] | None = None


def test_v3_message_tuple_unwraps_payload_and_preserves_metadata() -> None:
    event = adapt_v3_protocol_event(
        {
            "type": "event",
            "method": "messages",
            "params": {
                "namespace": ["tools:tc-1"],
                "timestamp": 1781294167426,
                "data": (
                    {
                        "event": "content-block-delta",
                        "index": 0,
                        "delta": {"type": "text-delta", "text": "hello"},
                    },
                    {"langgraph_node": "model", "run_id": "lc-run-1"},
                ),
            },
            "seq": 4,
            "event_id": "evt-4",
        },
        run_id="run-1",
        thread_id="thread-1",
    )

    assert event["method"] == "messages"
    assert event["namespace"] == ["tools:tc-1"]
    assert event["seq"] == 4
    assert event["upstream_event_id"] == "evt-4"
    assert event["timestamp"] == "1781294167426"
    assert event["data"]["event"] == "content-block-delta"
    assert event["data"]["metadata"] == {"langgraph_node": "model", "run_id": "lc-run-1"}
    assert to_assistant_ui_projection(event)["event"] == "messages|tools%3Atc-1"


def test_stream_mode_tuple_keeps_namespace_and_mode() -> None:
    event = adapt_stream_mode_chunk(
        (("tools:tc-1",), "values", {"messages": [{"content": "done"}]}),
        run_id="run-1",
        thread_id="thread-1",
        seq=8,
        event_id="fallback-8",
    )

    assert event["method"] == "values"
    assert event["namespace"] == ["tools:tc-1"]
    assert event["data"] == {"messages": [{"content": "done"}]}
    assert event["upstream_event_id"] == "fallback-8"


def test_unknown_method_becomes_named_custom_payload() -> None:
    event = adapt_v3_protocol_event(
        {
            "method": "a2a",
            "params": {"data": {"peer": "agent-b"}},
            "seq": 12,
        },
        run_id="run-1",
        thread_id="thread-1",
    )

    assert event["method"] == "custom"
    assert event["data"] == {"name": "a2a", "payload": {"peer": "agent-b"}}


def test_non_json_message_object_serializes_stable_fields() -> None:
    event = adapt_v3_protocol_event(
        {
            "method": "messages",
            "params": {
                "data": (
                    FakeChunk(
                        id="msg-1",
                        content="hi",
                        tool_call_chunks=[{"id": "call-1", "name": "search"}],
                    ),
                    {"langgraph_node": "model"},
                )
            },
            "seq": 2,
        },
        run_id="run-1",
        thread_id="thread-1",
    )

    assert event["data"]["id"] == "msg-1"
    assert event["data"]["type"] == "AIMessageChunk"
    assert event["data"]["content"] == "hi"
    assert event["data"]["tool_call_chunks"] == [{"id": "call-1", "name": "search"}]
    assert event["data"]["metadata"] == {"langgraph_node": "model"}


def test_subagent_discovery_normalizes_status_and_trigger() -> None:
    event = adapt_v3_protocol_event(
        {
            "method": "tasks",
            "params": {
                "namespace": ["tools:call-1"],
                "data": {
                    "id": "sub-1",
                    "name": "researcher",
                    "status": "completed",
                    "cause": {"type": "toolCall", "tool_call_id": "call-1"},
                    "task_input": "find docs",
                    "output": "done",
                },
            },
            "seq": 9,
        },
        run_id="run-1",
        thread_id="thread-1",
    )

    discovery = extract_subagent_discovery(event)

    assert discovery == {
        "id": "sub-1",
        "name": "researcher",
        "path": ["tools:call-1"],
        "status": "complete",
        "trigger_call_id": "call-1",
        "cause": {"type": "toolCall", "tool_call_id": "call-1"},
        "task_input": "find docs",
        "output": "done",
        "error": None,
    }


def test_synthesizes_tool_lifecycle_from_values_messages_without_duplicates() -> None:
    values = adapt_v3_protocol_event(
        {
            "method": "values",
            "params": {
                "data": {
                    "messages": [
                        {
                            "type": "ai",
                            "tool_calls": [
                                {"id": "call-1", "name": "search", "args": {"q": "deep agents"}}
                            ],
                        },
                        {
                            "type": "tool",
                            "tool_call_id": "call-2",
                            "content": "result",
                        },
                    ]
                }
            },
            "seq": 20,
            "event_id": "evt-20",
        },
        run_id="run-1",
        thread_id="thread-1",
    )
    seen = {"call-2"}

    events = synthesize_tool_events_from_values(values, seen_tool_call_ids=seen)

    assert [event["method"] for event in events] == ["tools"]
    assert events[0]["data"] == {
        "event": "tool-started",
        "tool_call_id": "call-1",
        "name": "search",
        "args": {"q": "deep agents"},
    }
    assert seen == {"call-1", "call-2"}
