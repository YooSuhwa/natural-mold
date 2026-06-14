from __future__ import annotations

from app.agent_runtime.protocol_events import stored_custom_protocol_event, stored_protocol_event
from app.agent_runtime.protocol_persistence import persistable_protocol_event


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
