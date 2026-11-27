from __future__ import annotations

from uuid import uuid4

import pytest

from app.agent_runtime.protocol_events import StoredProtocolEvent
from app.models.message_event import MessageEvent
from app.schemas.conversation_history import JsonValue
from app.services.ag_ui_journal_legacy import legacy_journal_events


@pytest.fixture
def record() -> MessageEvent:
    return MessageEvent(conversation_id=uuid4(), assistant_msg_id=str(uuid4()), events=[])


def _read(
    turn: MessageEvent, name: str, data: dict[str, JsonValue], ordinal: int = 1
) -> tuple[StoredProtocolEvent, ...] | None:
    return legacy_journal_events(
        turn, {"id": f"source-{ordinal}", "event": name, "data": data}, ordinal, ("secret-value",)
    )


def test_legacy_repeated_tokens_and_final_snapshot_share_one_turn_identity(
    record: MessageEvent,
) -> None:
    assert _read(record, "message_start", {"input": {"system": "private prompt"}}) == ()
    first = _read(record, "content_delta", {"delta": "반복"}, 2)
    record.linked_message_ids = [str(uuid4())]
    second = _read(record, "content_delta", {"delta": "반복"}, 3)
    end = _read(record, "message_end", {"content": "반복반복{", "status": "completed"}, 4)

    assert first and second and end
    assert first[0]["data"] == second[0]["data"]
    assert first[0]["data"] == {
        "id": end[0]["data"]["id"],
        "type": "AIMessageChunk",
        "content": "반복",
    }
    assert first[0]["id"] == "source-2"
    assert end[0]["method"] == "messages"
    assert end[0]["data"]["type"] == "ai"
    assert end[0]["data"]["content"] == "반복반복{"
    other = MessageEvent(conversation_id=uuid4(), assistant_msg_id=record.assistant_msg_id)
    separate = _read(other, "content_delta", {"delta": "반복"})
    assert separate and separate[0]["data"]["id"] != first[0]["data"]["id"]


def test_legacy_tool_requires_explicit_identity_and_uses_existing_redactor(
    record: MessageEvent,
) -> None:
    assert _read(record, "tool_call_result", {"tool_name": "search", "result": "orphan"}) is None
    call = _read(
        record,
        "tool_call_start",
        {
            "tool_name": "search",
            "tool_call_id": "call-1",
            "parameters": {"query": "find this", "other": "secret-value"},
            "debug": "never expose this",
        },
    )
    result = _read(
        record,
        "tool_call_result",
        {
            "tool_name": "search",
            "tool_call_id": "call-1",
            "result": "found it",
            "private": True,
        },
    )
    assert call and result
    assert call[0]["data"]["event"] == "tool-started"
    assert call[0]["data"]["args"]["query"] == "find this"
    assert "secret-value" not in str(call[0]["data"])
    assert "debug" not in call[0]["data"]
    assert result[0]["data"] == {
        "event": "tool-finished",
        "name": "search",
        "tool_call_id": "call-1",
        "content": "found it",
    }


def test_legacy_file_and_memory_events_expose_only_scoped_resource_references(
    record: MessageEvent,
) -> None:
    artifact = {"id": str(uuid4()), "version_id": str(uuid4()), "op": "created"}
    file_event = _read(
        record,
        "file_event",
        {**artifact, "conversation_id": str(record.conversation_id), "path": "/internal/private"},
    )
    assert file_event and file_event[0]["data"] == {"name": "file_event", "payload": artifact}
    assert _read(record, "file_event", {**artifact, "conversation_id": str(uuid4())}) is None
    memory_id = str(uuid4())
    event = _read(record, "memory_proposed", {"id": memory_id, "content": "sensitive note"})
    assert event and event[0]["data"] == {"name": "memory_proposed", "payload": {"id": memory_id}}


def test_old_unknown_debug_input_and_unconfirmed_interrupts_do_not_escape(
    record: MessageEvent,
) -> None:
    for name in ("future_private_channel", "message_start", "interrupt", "stale", "error"):
        assert _read(record, name, {"data": "private", "attempt": 1, "namespace": ["child"]}) == ()
    assert _read(record, "content_delta", {"delta": ["private", "opaque"]}) is None
    assert _read(record, "message_end", {"status": "completed"}) == ()


def test_memory_tool_result_does_not_replay_forgotten_or_unapproved_text(
    record: MessageEvent,
) -> None:
    for name in ("propose_memory", "search_memory"):
        events = _read(
            record,
            "tool_call_result",
            {"tool_name": name, "tool_call_id": "call-1", "result": '{"content":"private note"}'},
        )
        assert events and events[0]["data"]["content"] == "<redacted>"
