from __future__ import annotations

from app.agent_runtime.legacy_event_projection import project_protocol_event_to_legacy
from app.agent_runtime.protocol_events import stored_protocol_event


def test_message_delta_projects_to_legacy_content_delta() -> None:
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=1,
        event_id="evt-1",
        method="messages",
        data={
            "event": "content-block-delta",
            "delta": {"type": "text-delta", "text": "hello"},
        },
    )

    assert project_protocol_event_to_legacy(event) == [
        {"id": "evt-1", "event": "content_delta", "data": {"delta": "hello"}}
    ]


def test_tool_lifecycle_projects_to_legacy_tool_events() -> None:
    start = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=2,
        event_id="evt-2",
        method="tools",
        data={
            "event": "tool-started",
            "tool_call_id": "call-1",
            "name": "search",
            "args": {"q": "deep agents"},
        },
    )
    finish = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=3,
        event_id="evt-3",
        method="tools",
        data={
            "event": "tool-finished",
            "tool_call_id": "call-1",
            "name": "search",
            "content": {"ok": True},
        },
    )

    assert project_protocol_event_to_legacy(start) == [
        {
            "id": "evt-2",
            "event": "tool_call_start",
            "data": {
                "tool_call_id": "call-1",
                "tool_name": "search",
                "parameters": {"q": "deep agents"},
            },
        }
    ]
    assert project_protocol_event_to_legacy(finish) == [
        {
            "id": "evt-3",
            "event": "tool_call_result",
            "data": {
                "tool_call_id": "call-1",
                "tool_name": "search",
                "result": '{"ok":true}',
            },
        }
    ]


def test_final_values_message_projects_to_message_end_with_usage() -> None:
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=4,
        event_id="evt-4",
        method="values",
        data={
            "messages": [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": "done",
                    "usage_metadata": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cache_read_tokens": 2,
                        "estimated_cost": 0.001,
                    },
                },
            ]
        },
    )

    assert project_protocol_event_to_legacy(event) == [
        {
            "id": "evt-4",
            "event": "message_end",
            "data": {
                "content": "done",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "cache_creation_tokens": 0,
                    "cache_read_tokens": 2,
                    "estimated_cost": 0.001,
                },
            },
        }
    ]


def test_custom_artifact_and_memory_events_project_by_name() -> None:
    artifact = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=5,
        event_id="evt-5",
        method="custom",
        data={"name": "artifact", "payload": {"id": "file-1", "op": "created"}},
    )
    memory = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=6,
        event_id="evt-6",
        method="custom:memory_saved",
        data={"id": "mem-1"},
    )

    assert project_protocol_event_to_legacy(artifact) == [
        {"id": "evt-5", "event": "file_event", "data": {"id": "file-1", "op": "created"}}
    ]
    assert project_protocol_event_to_legacy(memory) == [
        {"id": "evt-6", "event": "memory_saved", "data": {"id": "mem-1"}}
    ]


def test_non_legacy_protocol_events_do_not_project() -> None:
    event = stored_protocol_event(
        run_id="run-1",
        thread_id="thread-1",
        seq=7,
        method="tasks",
        data={"name": "researcher", "status": "running"},
    )

    assert project_protocol_event_to_legacy(event) == []
