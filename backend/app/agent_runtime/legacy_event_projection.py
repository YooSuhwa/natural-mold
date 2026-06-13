from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypedDict

from app.agent_runtime import event_names
from app.agent_runtime.protocol_events import StoredProtocolEvent

LegacyEventName = Literal[
    "message_start",
    "content_delta",
    "tool_call_start",
    "tool_call_result",
    "file_event",
    "memory_proposed",
    "memory_saved",
    "memory_rejected",
    "memory_deleted",
    "message_end",
    "error",
    "interrupt",
    "stale",
]


class LegacySSEEvent(TypedDict):
    id: str
    event: LegacyEventName
    data: dict[str, Any]


def project_protocol_event_to_legacy(event: StoredProtocolEvent) -> list[LegacySSEEvent]:
    method = event["method"]
    if method == "messages":
        return _project_message_event(event)
    if method == "tools":
        return _project_tool_event(event)
    if method == "values":
        return _project_values_event(event)
    if method.startswith("custom") or method == "custom":
        return _project_custom_event(event)
    if method == "error":
        return [_legacy(event, event_names.ERROR, _as_mapping(event["data"]))]
    return []


def _project_message_event(event: StoredProtocolEvent) -> list[LegacySSEEvent]:
    data = _as_mapping(event["data"])
    event_name = _as_str(data.get("event"))
    if event_name == "message-start":
        message_id = _as_str(data.get("message_id") or data.get("id") or event["id"])
        return [_legacy(event, event_names.MESSAGE_START, {"id": message_id, "role": "assistant"})]

    text = _extract_text_delta(data)
    if text:
        return [_legacy(event, event_names.CONTENT_DELTA, {"delta": text})]

    return []


def _project_tool_event(event: StoredProtocolEvent) -> list[LegacySSEEvent]:
    data = _as_mapping(event["data"])
    tool_event = _as_str(data.get("event") or data.get("type"))
    tool_call_id = _as_str(data.get("tool_call_id") or data.get("id"))
    tool_name = _as_str(data.get("name") or data.get("tool_name") or "tool")

    if tool_event in {"tool-started", "tool-start", "start"}:
        parameters = data.get("args") if isinstance(data.get("args"), dict) else {}
        return [
            _legacy(
                event,
                event_names.TOOL_CALL_START,
                {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "parameters": parameters,
                },
            )
        ]

    if tool_event in {"tool-finished", "tool-error", "tool-result", "finish", "error"}:
        result = (
            data.get("result") or data.get("output") or data.get("content") or data.get("error")
        )
        return [
            _legacy(
                event,
                event_names.TOOL_CALL_RESULT,
                {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "result": _result_to_text(result),
                },
            )
        ]

    return []


def _project_values_event(event: StoredProtocolEvent) -> list[LegacySSEEvent]:
    data = _as_mapping(event["data"])
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return []

    final = _last_assistant_message(messages)
    if final is None:
        return []

    content = final.get("content")
    return [
        _legacy(
            event,
            event_names.MESSAGE_END,
            {
                "content": _result_to_text(content),
                "usage": _extract_usage(final),
            },
        )
    ]


def _project_custom_event(event: StoredProtocolEvent) -> list[LegacySSEEvent]:
    data = _as_mapping(event["data"])
    name = event["method"].removeprefix("custom:")
    if event["method"] == "custom":
        name = _as_str(data.get("name") or data.get("channel"))

    payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
    if name in {"artifact", "file", "file_event"}:
        return [_legacy(event, event_names.FILE_EVENT, dict(payload))]
    if name in {
        event_names.MEMORY_PROPOSED,
        event_names.MEMORY_SAVED,
        event_names.MEMORY_REJECTED,
        event_names.MEMORY_DELETED,
    }:
        return [_legacy(event, name, dict(payload))]
    if name == event_names.INTERRUPT:
        return [_legacy(event, event_names.INTERRUPT, dict(payload))]
    if name == event_names.STALE:
        return [_legacy(event, event_names.STALE, dict(payload))]
    return []


def _extract_text_delta(data: dict[str, Any]) -> str:
    delta = data.get("delta")
    if isinstance(delta, dict):
        text = delta.get("text")
        if isinstance(text, str):
            return text
    for key in ("text", "content", "delta"):
        value = data.get(key)
        if isinstance(value, str):
            return value
    return ""


def _last_assistant_message(messages: list[Any]) -> dict[str, Any] | None:
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        role = _as_str(message.get("role") or message.get("type")).lower()
        if role in {"assistant", "ai", "aimessage"}:
            return message
    return None


def _extract_usage(message: dict[str, Any]) -> dict[str, int | float]:
    usage = message.get("usage") or message.get("usage_metadata") or {}
    if not isinstance(usage, dict):
        return {}
    return {
        "prompt_tokens": _as_number(usage.get("prompt_tokens") or usage.get("input_tokens")),
        "completion_tokens": _as_number(
            usage.get("completion_tokens") or usage.get("output_tokens")
        ),
        "cache_creation_tokens": _as_number(usage.get("cache_creation_tokens")),
        "cache_read_tokens": _as_number(usage.get("cache_read_tokens")),
        **(
            {"estimated_cost": usage["estimated_cost"]}
            if isinstance(usage.get("estimated_cost"), int | float)
            else {}
        ),
    }


def _legacy(
    event: StoredProtocolEvent,
    name: str,
    data: dict[str, Any],
) -> LegacySSEEvent:
    return {
        "id": event["upstream_event_id"] or event["id"],
        "event": name,  # type: ignore[typeddict-item]
        "data": data,
    }


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if _is_payload_metadata_pair(value):
        payload = value[0]
        return dict(payload) if isinstance(payload, Mapping) else {}
    return {}


def _is_payload_metadata_pair(value: Any) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, str | bytes | bytearray)
        and len(value) == 2
        and isinstance(value[1], Mapping)
    )


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _as_number(value: Any) -> int | float:
    return value if isinstance(value, int | float) else 0


def _result_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
