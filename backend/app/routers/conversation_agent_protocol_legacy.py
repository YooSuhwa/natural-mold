from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import event_names
from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    stored_custom_protocol_event,
    stored_protocol_event,
)
from app.models.message_event import MessageEvent
from app.services import trace_storage


def protocol_events_from_legacy_sse(
    raw: Mapping[str, Any],
    *,
    record: MessageEvent,
    seq: int,
) -> list[StoredProtocolEvent]:
    name = raw.get("event")
    data = _as_mapping(raw.get("data"))
    raw_id = _optional_str(raw.get("id"))

    match name:
        case event_names.MESSAGE_END:
            return [
                _legacy_protocol_event(
                    record,
                    seq=seq,
                    method="lifecycle",
                    data=_terminal_lifecycle_data(record, data),
                    event_id=_derived_event_id(raw_id, "lifecycle"),
                )
            ]
        case event_names.STALE:
            return [
                _legacy_protocol_event(
                    record,
                    seq=seq,
                    method="lifecycle",
                    data={
                        **data,
                        "event": event_names.STALE,
                        "status": "error",
                        "run_id": data.get("run_id") or record.assistant_msg_id,
                    },
                    event_id=_derived_event_id(raw_id, "lifecycle"),
                ),
                _legacy_custom_protocol_event(
                    record,
                    seq=seq,
                    name=event_names.STALE,
                    payload={
                        **data,
                        "run_id": data.get("run_id") or record.assistant_msg_id,
                    },
                    event_id=_derived_event_id(raw_id, event_names.STALE),
                ),
            ]
        case event_names.ERROR:
            return [
                _legacy_protocol_event(
                    record,
                    seq=seq,
                    method="error",
                    data=data,
                    event_id=_derived_event_id(raw_id, "error"),
                )
            ]
        case _:
            return []


async def legacy_state_messages(
    db: AsyncSession,
    conversation_id: uuid.UUID,
) -> list[dict[str, Any]]:
    records = await trace_storage.get_traces_for_conversation(db, conversation_id)
    messages: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for record in records:
        for message in _messages_from_record(record):
            message_id = _optional_str(message.get("id"))
            if message_id is not None:
                if message_id in seen_ids:
                    continue
                seen_ids.add(message_id)
            messages.append(message)
    return messages


def _messages_from_record(record: MessageEvent) -> list[dict[str, Any]]:
    events = list(record.events or [])
    if not events:
        return []

    messages: list[dict[str, Any]] = []
    start = _first_event(events, event_names.MESSAGE_START)
    input_payload = _as_mapping(_as_mapping(start.get("data") if start else None).get("input"))
    for index, message in enumerate(_input_messages(input_payload)):
        messages.append(_state_message_from_input(message, record=record, index=index))

    assistant = _assistant_message_from_events(record, events, start)
    if assistant is not None:
        messages.append(assistant)
    return messages


def _assistant_message_from_events(
    record: MessageEvent,
    events: list[dict[str, Any]],
    start: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    end = _first_event(events, event_names.MESSAGE_END, reverse=True)
    end_data = _as_mapping(end.get("data") if end else None)
    content = _optional_str(end_data.get("content")) or _content_from_deltas(events)
    if not content:
        return None
    start_data = _as_mapping(start.get("data") if start else None)
    message_id = _optional_str(start_data.get("id")) or record.assistant_msg_id
    message: dict[str, Any] = {"type": "ai", "content": content, "id": message_id}
    usage = _usage_metadata(end_data.get("usage"))
    if usage:
        message["usage_metadata"] = usage
    return message


def _state_message_from_input(
    message: Mapping[str, Any],
    *,
    record: MessageEvent,
    index: int,
) -> dict[str, Any]:
    role = _optional_str(message.get("role") or message.get("type")) or "user"
    msg_type = "human" if role in {"user", "human"} else role
    return {
        "type": msg_type,
        "content": _content_to_text(message.get("content")),
        "id": _optional_str(message.get("id")) or f"legacy:{record.assistant_msg_id}:input:{index}",
    }


def _input_messages(input_payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    messages = input_payload.get("messages")
    if not isinstance(messages, list):
        return []
    return [item for item in messages if isinstance(item, Mapping)]


def _first_event(
    events: list[dict[str, Any]],
    name: str,
    *,
    reverse: bool = False,
) -> dict[str, Any] | None:
    iterable = reversed(events) if reverse else events
    for event in iterable:
        if event.get("event") == name:
            return event
    return None


def _content_from_deltas(events: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for event in events:
        if event.get("event") != event_names.CONTENT_DELTA:
            continue
        data = _as_mapping(event.get("data"))
        text = _optional_str(data.get("delta") or data.get("content"))
        if text:
            parts.append(text)
    return "".join(parts)


def _terminal_lifecycle_data(record: MessageEvent, data: Mapping[str, Any]) -> dict[str, Any]:
    status = _optional_str(data.get("status")) or "completed"
    return {
        **dict(data),
        "event": status,
        "status": status,
        "run_id": record.assistant_msg_id,
    }


def _legacy_protocol_event(
    record: MessageEvent,
    *,
    seq: int,
    method: str,
    data: dict[str, Any],
    event_id: str | None,
) -> StoredProtocolEvent:
    return stored_protocol_event(
        run_id=record.assistant_msg_id,
        thread_id=str(record.conversation_id),
        seq=seq,
        method=method,
        data=data,
        event_id=event_id,
        id=event_id,
    )


def _legacy_custom_protocol_event(
    record: MessageEvent,
    *,
    seq: int,
    name: str,
    payload: dict[str, Any],
    event_id: str | None,
) -> StoredProtocolEvent:
    return stored_custom_protocol_event(
        run_id=record.assistant_msg_id,
        thread_id=str(record.conversation_id),
        seq=seq,
        name=name,
        payload=payload,
        event_id=event_id,
        id=event_id,
    )


def _usage_metadata(raw: Any) -> dict[str, Any]:
    usage = _as_mapping(raw)
    if not usage:
        return {}
    input_tokens = _int_value(usage.get("prompt_tokens"))
    output_tokens = _int_value(usage.get("completion_tokens"))
    cache_creation = _int_value(usage.get("cache_creation_tokens"))
    cache_read = _int_value(usage.get("cache_read_tokens"))
    if input_tokens == 0 and output_tokens == 0 and cache_creation == 0 and cache_read == 0:
        return {}
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_token_details": {
            "cache_creation": cache_creation,
            "cache_read": cache_read,
        },
    }


def _content_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return "" if value is None else str(value)


def _derived_event_id(raw_id: str | None, suffix: str) -> str | None:
    return f"{raw_id}:{suffix}" if raw_id else None


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_value(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}
