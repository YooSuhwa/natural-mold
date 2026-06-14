from __future__ import annotations

from app.agent_runtime import event_names
from app.agent_runtime.ag_ui_adapter import (
    brokered_moldy_event_to_ag_ui_events,
    slice_ag_ui_events_after,
)
from app.agent_runtime.protocol_events import stored_protocol_event


def test_canonical_protocol_message_delta_maps_to_ag_ui_text_content() -> None:
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=1,
        method="messages",
        data={
            "event": "content-block-delta",
            "delta": {"type": "text-delta", "text": "canonical text"},
        },
        event_id="proto-1",
    )

    events = brokered_moldy_event_to_ag_ui_events(event, thread_id="thread-1", run_id="run-1")

    assert [evt["event"] for evt in events] == ["TEXT_MESSAGE_CONTENT"]
    assert events[0]["id"] == "proto-1:ag:0"
    assert events[0]["data"]["messageId"] == "run-1"
    assert events[0]["data"]["delta"] == "canonical text"
    assert events[0]["data"]["rawEvent"]["data"] == event["data"]


def test_canonical_protocol_tool_events_map_to_ag_ui_tool_lifecycle() -> None:
    start = {
        "id": "wire-tool-1",
        "event": "message",
        "data": {
            "type": "event",
            "method": "tools",
            "params": {
                "namespace": [],
                "data": {
                    "event": "tool-started",
                    "tool_call_id": "call-1",
                    "name": "search",
                    "args": {"q": "moldy"},
                },
            },
            "seq": 2,
            "event_id": "wire-tool-1",
        },
    }
    finish = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=3,
        method="tools",
        data={
            "event": "tool-finished",
            "tool_call_id": "call-1",
            "name": "search",
            "content": {"ok": True},
        },
        event_id="proto-tool-2",
    )
    error = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=4,
        method="tools",
        data={
            "event": "tool-error",
            "tool_call_id": "call-2",
            "tool_name": "browser",
            "error": "timeout",
        },
        event_id="proto-tool-3",
    )

    events = list(
        slice_ag_ui_events_after([start, finish, error], None, thread_id="thread-1", run_id="run-1")
    )

    assert [evt["event"] for evt in events] == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "TOOL_CALL_RESULT",
    ]
    assert events[0]["data"]["toolCallId"] == "call-1"
    assert events[0]["data"]["toolCallName"] == "search"
    assert events[1]["data"]["delta"] == '{"q": "moldy"}'
    assert events[3]["data"]["content"] == '{"ok":true}'
    assert events[4]["data"]["toolCallId"] == "call-2"
    assert events[4]["data"]["content"] == "timeout"
    assert events[4]["data"]["isError"] is True


def test_canonical_protocol_custom_file_and_memory_events_keep_moldy_names() -> None:
    file_event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=5,
        method="custom:file_event",
        data={"op": "created", "id": "artifact-1", "path": "report.md"},
        event_id="proto-file-1",
    )
    memory_event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=6,
        method="custom:memory_saved",
        data={"id": "memory-1", "scope": "user"},
        event_id="proto-memory-1",
    )

    events = list(
        slice_ag_ui_events_after(
            [file_event, memory_event],
            None,
            thread_id="thread-1",
            run_id="run-1",
        )
    )

    assert [evt["data"]["name"] for evt in events] == [
        "moldy.file_event",
        "moldy.memory_saved",
    ]
    assert events[0]["data"]["value"]["payload"]["path"] == "report.md"
    assert events[1]["data"]["value"]["payload"]["id"] == "memory-1"


def test_canonical_protocol_reasoning_delta_maps_to_custom_event() -> None:
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=7,
        method="messages",
        data={
            "event": "content-block-delta",
            "delta": {"type": "reasoning-delta", "summary": "checking sources"},
        },
        event_id="proto-reasoning-1",
    )

    events = brokered_moldy_event_to_ag_ui_events(event, thread_id="thread-1", run_id="run-1")

    assert [evt["event"] for evt in events] == ["CUSTOM"]
    assert events[0]["data"]["name"] == "moldy.reasoning"
    assert events[0]["data"]["value"]["payload"]["delta"]["summary"] == "checking sources"


def test_canonical_protocol_thinking_block_maps_to_custom_event() -> None:
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=8,
        method="messages",
        data={
            "event": "content-block-start",
            "content_block": {"type": "thinking", "summary": "planning next step"},
        },
        event_id="proto-thinking-1",
    )

    events = brokered_moldy_event_to_ag_ui_events(event, thread_id="thread-1", run_id="run-1")

    assert [evt["event"] for evt in events] == ["CUSTOM"]
    assert events[0]["data"]["name"] == "moldy.reasoning"
    assert events[0]["data"]["value"]["payload"]["content_block"]["summary"] == "planning next step"


def test_slice_ag_ui_events_after_handles_mixed_moldy_and_canonical_protocol_events() -> None:
    source_events = [
        {
            "id": "old-1",
            "event": event_names.CONTENT_DELTA,
            "data": {"delta": "legacy"},
        },
        stored_protocol_event(
            run_id="run-1",
            thread_id="thread-1",
            seq=8,
            method="messages",
            data={"chunk": "canonical"},
            event_id="proto-mixed-1",
        ),
    ]

    resumed = list(
        slice_ag_ui_events_after(
            source_events,
            "old-1:ag:0",
            thread_id="thread-1",
            run_id="run-1",
        )
    )

    assert [evt["event"] for evt in resumed] == ["TEXT_MESSAGE_CONTENT"]
    assert resumed[0]["id"] == "proto-mixed-1:ag:0"
    assert resumed[0]["data"]["delta"] == "canonical"
