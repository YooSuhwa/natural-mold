from __future__ import annotations

from dataclasses import dataclass

from langgraph.types import Interrupt

from app.agent_runtime.langgraph_protocol_adapter import (
    adapt_stream_mode_chunk,
    adapt_v3_protocol_event,
    extract_subagent_discovery,
    synthesize_tool_events_from_values,
)
from app.agent_runtime.protocol_events import (
    canonical_input_requested_events,
    protocol_interrupts_from_event,
    to_assistant_ui_projection,
)


@dataclass
class FakeChunk:
    id: str
    content: str
    type: str = "AIMessageChunk"
    tool_call_chunks: list[dict[str, object]] | None = None


def test_v3_message_tuple_preserves_sdk_payload_metadata_pair() -> None:
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
                    {
                        "langgraph_node": "model",
                        "run_id": "lc-run-1",
                        "checkpoint_id": "ck-1",
                        "checkpoint_ns": "model",
                    },
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
    assert event["checkpoint_id"] == "ck-1"
    assert event["checkpoint_ns"] == "model"
    assert event["timestamp"] == "1781294167426"
    assert event["data"] == [
        {
            "event": "content-block-delta",
            "index": 0,
            "delta": {"type": "text-delta", "text": "hello"},
        },
        {
            "langgraph_node": "model",
            "run_id": "lc-run-1",
            "checkpoint_id": "ck-1",
            "checkpoint_ns": "model",
        },
    ]
    assert to_assistant_ui_projection(event)["event"] == "messages|tools%3Atc-1"


def test_v3_reasoning_content_is_redacted_before_storage() -> None:
    event = adapt_v3_protocol_event(
        {
            "type": "event",
            "method": "messages",
            "params": {
                "data": {
                    "id": "assistant-reasoning-1",
                    "type": "AIMessageChunk",
                    "content": [
                        {"type": "text", "text": "safe answer"},
                        {
                            "type": "reasoning",
                            "text": "private hidden reasoning",
                            "summary": "checked policy",
                        },
                    ],
                    "additional_kwargs": {"reasoning": "private hidden kwargs"},
                },
            },
            "seq": 5,
        },
        run_id="run-1",
        thread_id="thread-1",
    )

    assert event["data"]["content"] == [
        {"type": "text", "text": "safe answer"},
        {"type": "reasoning", "summary": "checked policy", "redacted": True},
    ]
    assert event["data"]["additional_kwargs"] == {"reasoning": "[redacted]"}


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


def test_custom_prefixed_method_becomes_standard_named_custom_payload() -> None:
    event = adapt_v3_protocol_event(
        {
            "method": "custom:usage",
            "params": {"data": {"prompt_tokens": 1, "completion_tokens": 2}},
            "seq": 13,
        },
        run_id="run-1",
        thread_id="thread-1",
    )

    assert event["method"] == "custom"
    assert event["data"] == {
        "name": "usage",
        "payload": {"prompt_tokens": 1, "completion_tokens": 2},
    }


def test_v3_input_requested_preserves_protocol_interrupt_method() -> None:
    event = adapt_v3_protocol_event(
        {
            "type": "event",
            "method": "input.requested",
            "params": {
                "namespace": ["tools:call-1"],
                "data": {
                    "interrupt_id": "intr-1",
                    "payload": {"action_requests": [], "review_configs": []},
                },
            },
            "seq": 5,
            "event_id": "evt-input-1",
        },
        run_id="run-1",
        thread_id="thread-1",
    )

    assert event["method"] == "input.requested"
    assert event["namespace"] == ["tools:call-1"]
    assert event["data"]["interrupt_id"] == "intr-1"
    assert event["data"]["payload"] == {"action_requests": [], "review_configs": []}


def test_v3_values_preserves_langgraph_interrupt_value_and_id() -> None:
    event = adapt_v3_protocol_event(
        {
            "type": "event",
            "method": "values",
            "params": {
                "namespace": ["tools:call-1"],
                "data": {
                    "__interrupt__": (
                        Interrupt(
                            value={"question": "approve execute_in_skill?"},
                            id="intr-1",
                        ),
                    )
                },
            },
            "seq": 6,
            "event_id": "evt-values-interrupt",
        },
        run_id="run-1",
        thread_id="thread-1",
    )

    assert event["method"] == "values"
    assert event["data"]["__interrupt__"] == [
        {"value": {"question": "approve execute_in_skill?"}, "id": "intr-1"}
    ]


def test_v3_values_merges_params_interrupts_into_protocol_data() -> None:
    event = adapt_v3_protocol_event(
        {
            "type": "event",
            "method": "values",
            "params": {
                "namespace": ["tools:call-1"],
                "data": {"messages": [{"content": "waiting"}]},
                "interrupts": [
                    Interrupt(
                        value={"question": "approve web_search?"},
                        id="intr-params-1",
                    )
                ],
            },
            "seq": 7,
            "event_id": "evt-values-params-interrupt",
        },
        run_id="run-1",
        thread_id="thread-1",
    )

    assert event["data"]["messages"] == [{"content": "waiting"}]
    assert event["data"]["__interrupt__"] == [
        {"value": {"question": "approve web_search?"}, "id": "intr-params-1"}
    ]
    assert protocol_interrupts_from_event(event)[0]["id"] == "intr-params-1"
    assert canonical_input_requested_events(event)[0]["method"] == "input.requested"


def test_v3_values_does_not_duplicate_params_interrupts_when_data_has_interrupts() -> None:
    event = adapt_v3_protocol_event(
        {
            "type": "event",
            "method": "values",
            "params": {
                "data": {
                    "__interrupt__": [
                        Interrupt(
                            value={"question": "approve existing?"},
                            id="intr-existing",
                        )
                    ]
                },
                "interrupts": [
                    Interrupt(
                        value={"question": "approve duplicate?"},
                        id="intr-params",
                    )
                ],
            },
            "seq": 8,
            "event_id": "evt-values-duplicate-interrupt",
        },
        run_id="run-1",
        thread_id="thread-1",
    )

    assert event["data"]["__interrupt__"] == [
        {"value": {"question": "approve existing?"}, "id": "intr-existing"}
    ]
    assert [interrupt["id"] for interrupt in protocol_interrupts_from_event(event)] == [
        "intr-existing"
    ]


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

    assert event["data"] == [
        {
            "id": "msg-1",
            "type": "AIMessageChunk",
            "content": "hi",
            "tool_call_chunks": [{"id": "call-1", "name": "search"}],
        },
        {"langgraph_node": "model"},
    ]


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
    assert seen == {"start:call-1", "call-2"}
