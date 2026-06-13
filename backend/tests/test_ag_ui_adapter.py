from __future__ import annotations

from app.agent_runtime import event_names
from app.agent_runtime.ag_ui_adapter import (
    brokered_moldy_event_to_ag_ui_events,
    slice_ag_ui_events_after,
    source_event_id_from_ag_ui,
)


def test_message_start_maps_to_run_and_text_start() -> None:
    events = brokered_moldy_event_to_ag_ui_events(
        {
            "id": "run-1-1",
            "event": event_names.MESSAGE_START,
            "data": {"id": "run-1", "role": "assistant"},
        },
        thread_id="thread-1",
        run_id="run-1",
    )

    assert [evt["event"] for evt in events] == ["RUN_STARTED", "TEXT_MESSAGE_START"]
    assert [evt["id"] for evt in events] == ["run-1-1:ag:0", "run-1-1:ag:1"]
    assert events[0]["data"] == {
        "type": "RUN_STARTED",
        "threadId": "thread-1",
        "runId": "run-1",
    }
    assert events[1]["data"]["messageId"] == "run-1"
    assert events[1]["data"]["role"] == "assistant"


def test_text_message_lifecycle_shares_run_scoped_message_id() -> None:
    """START/CONTENT/END 는 같은 messageId 를 공유해야 표준 client 가 매칭한다.

    message_start 의 ``data.id`` 가 run_id 와 다른 경우에도 세 이벤트 모두
    run_id 로 통일되고, 원본 id 는 rawEvent 로 보존된다.
    """
    start = brokered_moldy_event_to_ag_ui_events(
        {
            "id": "src-1",
            "event": event_names.MESSAGE_START,
            "data": {"id": "assistant-msg-uuid", "role": "assistant"},
        },
        thread_id="thread-1",
        run_id="run-1",
    )
    content = brokered_moldy_event_to_ag_ui_events(
        {
            "id": "src-2",
            "event": event_names.CONTENT_DELTA,
            "data": {"delta": "hi"},
        },
        thread_id="thread-1",
        run_id="run-1",
    )
    end = brokered_moldy_event_to_ag_ui_events(
        {
            "id": "src-3",
            "event": event_names.MESSAGE_END,
            "data": {"content": "hi", "usage": {}, "status": "completed"},
        },
        thread_id="thread-1",
        run_id="run-1",
    )

    message_ids = {
        start[1]["data"]["messageId"],
        content[0]["data"]["messageId"],
        end[0]["data"]["messageId"],
    }
    assert message_ids == {"run-1"}
    assert start[1]["data"]["rawEvent"]["id"] == "assistant-msg-uuid"


def test_error_event_omits_code_key_when_absent() -> None:
    events = brokered_moldy_event_to_ag_ui_events(
        {
            "id": "src-err",
            "event": event_names.ERROR,
            "data": {"message": "boom"},
        },
        thread_id="thread-1",
        run_id="run-1",
    )

    assert events[0]["event"] == "RUN_ERROR"
    assert "code" not in events[0]["data"]

    coded = brokered_moldy_event_to_ag_ui_events(
        {
            "id": "src-err-2",
            "event": event_names.ERROR,
            "data": {"message": "boom", "code": "llm_credential_required"},
        },
        thread_id="thread-1",
        run_id="run-1",
    )
    assert coded[0]["data"]["code"] == "llm_credential_required"


def test_message_end_preserves_usage_and_terminal_status() -> None:
    events = brokered_moldy_event_to_ag_ui_events(
        {
            "id": "run-1-3",
            "event": event_names.MESSAGE_END,
            "data": {
                "content": "done",
                "usage": {"prompt_tokens": 3},
                "status": "completed",
            },
        },
        thread_id="thread-1",
        run_id="run-1",
    )

    assert [evt["event"] for evt in events] == ["TEXT_MESSAGE_END", "RUN_FINISHED"]
    assert events[0]["data"]["rawEvent"]["usage"]["prompt_tokens"] == 3
    assert events[1]["data"]["result"] == {"status": "completed"}


def test_tool_call_start_emits_ag_ui_tool_lifecycle_with_full_args() -> None:
    events = brokered_moldy_event_to_ag_ui_events(
        {
            "id": "run-1-2",
            "event": event_names.TOOL_CALL_START,
            "data": {
                "tool_call_id": "tc-1",
                "tool_name": "web_search",
                "parameters": {"query": "moldy"},
            },
        },
        thread_id="thread-1",
        run_id="run-1",
    )

    assert [evt["event"] for evt in events] == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
    ]
    assert events[0]["data"]["toolCallId"] == "tc-1"
    assert events[0]["data"]["toolCallName"] == "web_search"
    assert events[1]["data"]["delta"] == '{"query": "moldy"}'
    assert events[2]["data"]["toolCallId"] == "tc-1"


def test_custom_events_preserve_moldy_payload() -> None:
    events = brokered_moldy_event_to_ag_ui_events(
        {
            "id": "run-1-4",
            "event": event_names.INTERRUPT,
            "data": {"interrupt_id": "hitl-1", "action_requests": [], "review_configs": []},
        },
        thread_id="thread-1",
        run_id="run-1",
    )

    assert events[0]["event"] == "CUSTOM"
    assert events[0]["data"]["name"] == "moldy.interrupt"
    assert events[0]["data"]["value"]["payload"]["interrupt_id"] == "hitl-1"


def test_historical_moldy_content_delta_still_maps_to_ag_ui_text_content() -> None:
    events = brokered_moldy_event_to_ag_ui_events(
        {
            "id": "old-1",
            "event": event_names.CONTENT_DELTA,
            "data": {"delta": "legacy text"},
        },
        thread_id="thread-1",
        run_id="run-1",
    )

    assert events[0]["event"] == "TEXT_MESSAGE_CONTENT"
    assert events[0]["data"]["delta"] == "legacy text"
    assert events[0]["data"]["rawEvent"] == {"delta": "legacy text"}


def test_slice_ag_ui_events_after_resumes_within_split_source_event() -> None:
    source_events = [
        {
            "id": "run-1-1",
            "event": event_names.MESSAGE_START,
            "data": {"id": "run-1", "role": "assistant"},
        },
        {
            "id": "run-1-2",
            "event": event_names.CONTENT_DELTA,
            "data": {"delta": "hello"},
        },
    ]

    resumed = list(
        slice_ag_ui_events_after(
            source_events,
            "run-1-1:ag:0",
            thread_id="thread-1",
            run_id="run-1",
        )
    )

    assert [evt["event"] for evt in resumed] == ["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT"]
    assert source_event_id_from_ag_ui("run-1-1:ag:0") == "run-1-1"
