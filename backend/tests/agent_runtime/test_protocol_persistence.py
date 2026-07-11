from __future__ import annotations

from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    stored_custom_protocol_event,
    stored_protocol_event,
)
from app.agent_runtime.protocol_persistence import (
    persistable_protocol_event,
    persistable_wire_protocol_event,
)
from app.agent_runtime.protocol_redaction import redact_protocol_data


def _as_wire(event: StoredProtocolEvent) -> StoredProtocolEvent:
    """emit()이 만드는 wire 뷰 재현 — value/key redaction 완료, memory 는 유지."""
    return {
        **event,
        "data": redact_protocol_data(event["method"], event["data"], redact_memory=False),
    }


def test_persistable_protocol_event_redacts_sensitive_tool_args() -> None:
    event = stored_protocol_event(
        run_id="run-secret",
        thread_id="thread-secret",
        seq=1,
        method="tools",
        data={
            "name": "http_request",
            "args": {
                "api_key": "SECRET_VALUE",
                "query": "safe",
                "nested": {"refresh_token": "REFRESH_SECRET"},
            },
        },
    )

    persisted = persistable_protocol_event(event)

    assert persisted["data"]["args"] == {
        "api_key": "<redacted>",
        "query": "safe",
        "nested": {"refresh_token": "<redacted>"},
    }
    assert "SECRET_VALUE" not in repr(persisted)
    assert "REFRESH_SECRET" not in repr(persisted)


def test_persistable_protocol_event_preserves_token_usage_metrics() -> None:
    event = stored_custom_protocol_event(
        run_id="run-usage",
        thread_id="thread-usage",
        seq=1,
        name="usage",
        payload={
            "prompt_tokens": 120,
            "completion_tokens": 45,
            "cache_creation_tokens": 10,
            "cache_read_tokens": 20,
            "total_tokens": 195,
            "access_token": "SECRET_VALUE",
        },
    )

    persisted = persistable_protocol_event(event)

    assert persisted["data"]["payload"] == {
        "prompt_tokens": 120,
        "completion_tokens": 45,
        "cache_creation_tokens": 10,
        "cache_read_tokens": 20,
        "total_tokens": 195,
        "access_token": "<redacted>",
    }
    assert "SECRET_VALUE" not in repr(persisted)


def test_persistable_protocol_event_redacts_sensitive_json_string_args() -> None:
    event = stored_protocol_event(
        run_id="run-secret-string",
        thread_id="thread-secret-string",
        seq=1,
        method="messages",
        data=[
            {
                "event": "content-block-delta",
                "delta": {
                    "type": "block-delta",
                    "fields": {
                        "type": "tool_call_chunk",
                        "name": "execute_in_skill",
                        "args": (
                            '{"command": "node scripts/create.cjs", '
                            '"api_key": "SECRET_VALUE", '
                            '"usage_metadata": {"prompt_tokens": 12}}'
                        ),
                    },
                },
            }
        ],
    )

    persisted = persistable_protocol_event(event)
    args = persisted["data"][0]["delta"]["fields"]["args"]

    assert "SECRET_VALUE" not in repr(persisted)
    assert '"api_key":"<redacted>"' in args
    assert '"prompt_tokens":12' in args


def test_persistable_protocol_event_redacts_sensitive_repr_string_args() -> None:
    event = stored_protocol_event(
        run_id="run-secret-repr",
        thread_id="thread-secret-repr",
        seq=1,
        method="input.requested",
        data={
            "description": (
                "Args: {'command': 'node scripts/create.cjs', "
                "'api_key': 'SECRET_VALUE', 'prompt_tokens': 12}"
            )
        },
    )

    persisted = persistable_protocol_event(event)

    assert "SECRET_VALUE" not in repr(persisted)
    assert "<redacted>" in persisted["data"]["description"]
    assert "prompt_tokens" in persisted["data"]["description"]


def test_persistable_protocol_event_redacts_memory_tool_arguments() -> None:
    event = stored_protocol_event(
        run_id="run-memory",
        thread_id="thread-memory",
        seq=1,
        method="tools",
        data={
            "name": "save_user_memory",
            "args": {
                "content": "private user memory",
                "reason": "mentioned in conversation",
                "scope": "user",
            },
        },
    )

    persisted = persistable_protocol_event(event)

    assert persisted["data"]["args"] == {
        "content": "<redacted>",
        "reason": "<redacted>",
        "scope": "user",
    }


def test_persistable_protocol_event_redacts_memory_custom_payload() -> None:
    event = stored_custom_protocol_event(
        run_id="run-memory",
        thread_id="thread-memory",
        seq=2,
        name="memory_saved",
        payload={
            "id": "memory-1",
            "content": "private user memory",
            "reason": "mentioned in conversation",
        },
    )

    persisted = persistable_protocol_event(event)

    assert persisted["data"]["payload"] == {
        "id": "memory-1",
        "content": "<redacted>",
        "reason": "<redacted>",
    }


# --------------------------------------------------------------------------
# BE-P5(b) — persistable_wire_protocol_event (wire redaction 재사용 hot path)
# --------------------------------------------------------------------------


def test_persistable_wire_protocol_event_masks_memory_from_wire_view() -> None:
    """wire 뷰는 기억 내용을 유지하지만 persist 는 마스킹한다 (W2-3 계약)."""
    event = stored_custom_protocol_event(
        run_id="run-wire-memory",
        thread_id="thread-wire-memory",
        seq=1,
        name="moldy.memory_recalled",
        payload={"memories": [{"id": "m1", "scope": "user", "content": "한국어 선호"}]},
    )
    wire = _as_wire(event)
    assert wire["data"]["payload"]["memories"][0]["content"] == "한국어 선호"

    persisted = persistable_wire_protocol_event(wire)

    assert persisted["data"]["payload"]["memories"][0]["content"] == "<redacted>"
    assert persisted["data"]["payload"]["memories"][0]["id"] == "m1"


def test_persistable_wire_protocol_event_masks_memory_tool_args() -> None:
    event = stored_protocol_event(
        run_id="run-wire-memtool",
        thread_id="thread-wire-memtool",
        seq=1,
        method="tools",
        data={
            "name": "save_user_memory",
            "args": {"content": "private user memory", "reason": "why", "scope": "user"},
        },
    )

    persisted = persistable_wire_protocol_event(_as_wire(event))

    assert persisted["data"]["args"] == {
        "content": "<redacted>",
        "reason": "<redacted>",
        "scope": "user",
    }


def test_persistable_wire_protocol_event_compacts_values_snapshot() -> None:
    event = stored_protocol_event(
        run_id="run-wire-values",
        thread_id="thread-wire-values",
        seq=1,
        method="values",
        data={
            "messages": [
                {
                    "id": "msg-1",
                    "type": "ai",
                    "content": "large assistant text",
                    "additional_kwargs": {"reasoning": "private chain"},
                }
            ],
            "todos": [{"id": "todo-1", "content": "ship", "status": "in_progress"}],
        },
    )

    persisted = persistable_wire_protocol_event(_as_wire(event))

    assert persisted["data"] == {
        "messages": [{"id": "msg-1", "type": "ai"}],
        "todos": [{"id": "todo-1", "content": "ship", "status": "in_progress"}],
    }


def test_persistable_wire_protocol_event_matches_full_variant() -> None:
    """full 변형(raw 입력)과 wire 변형(wire 입력)은 같은 persisted 형태를
    만든다 — 두 구현이 갈라지면 hot path 만 계약을 잃는 회귀를 잡는다."""
    events = [
        stored_protocol_event(
            run_id="run-eq",
            thread_id="thread-eq",
            seq=1,
            method="tools",
            data={"name": "http_request", "args": {"api_key": "SECRET_VALUE", "query": "safe"}},
        ),
        stored_custom_protocol_event(
            run_id="run-eq",
            thread_id="thread-eq",
            seq=2,
            name="memory_saved",
            payload={"id": "m1", "content": "private", "reason": "why"},
        ),
        stored_protocol_event(
            run_id="run-eq",
            thread_id="thread-eq",
            seq=3,
            method="values",
            data={"messages": [{"id": "msg-1", "type": "ai", "content": "text"}], "todos": []},
        ),
    ]
    for event in events:
        assert persistable_wire_protocol_event(_as_wire(event)) == persistable_protocol_event(event)
