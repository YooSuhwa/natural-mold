from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_protocol_event


def synthesize_tool_events_from_values(
    values_event: StoredProtocolEvent,
    *,
    seen_tool_call_ids: set[str] | None = None,
) -> list[StoredProtocolEvent]:
    if values_event["method"] != "values" or not isinstance(values_event["data"], Mapping):
        return []

    messages = values_event["data"].get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, str | bytes):
        return []

    seen = seen_tool_call_ids if seen_tool_call_ids is not None else set()
    synthesized: list[StoredProtocolEvent] = []
    for index, message in enumerate(messages):
        normalized = _serialize_value(message)
        if not isinstance(normalized, Mapping):
            continue
        synthesized.extend(
            _synthesize_from_message(
                normalized,
                source_event=values_event,
                index=index,
                seen=seen,
            )
        )
    return synthesized


def _synthesize_from_message(
    message: Mapping[str, Any],
    *,
    source_event: StoredProtocolEvent,
    index: int,
    seen: set[str],
) -> list[StoredProtocolEvent]:
    events: list[StoredProtocolEvent] = []
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, Sequence) and not isinstance(tool_calls, str | bytes):
        for call in tool_calls:
            if not isinstance(call, Mapping):
                continue
            call_id = _coerce_optional_str(call.get("id"))
            if not call_id or _has_seen_tool_event(seen, kind="start", call_id=call_id):
                continue
            _mark_seen_tool_event(seen, kind="start", call_id=call_id)
            events.append(
                _synthetic_tool_event(
                    source_event,
                    index=index,
                    tool_call_id=call_id,
                    event_name="tool-started",
                    data={
                        "event": "tool-started",
                        "tool_call_id": call_id,
                        "name": call.get("name"),
                        "args": _serialize_value(call.get("args")),
                    },
                )
            )

    tool_call_id = _coerce_optional_str(message.get("tool_call_id"))
    message_type = _coerce_optional_str(message.get("type"))
    if (
        tool_call_id
        and not _has_seen_tool_event(seen, kind="finish", call_id=tool_call_id)
        and message_type in {"tool", "ToolMessage"}
    ):
        _mark_seen_tool_event(seen, kind="finish", call_id=tool_call_id)
        events.append(
            _synthetic_tool_event(
                source_event,
                index=index,
                tool_call_id=tool_call_id,
                event_name="tool-finished",
                data={
                    "event": "tool-finished",
                    "tool_call_id": tool_call_id,
                    "name": message.get("name"),
                    "content": _serialize_value(message.get("content")),
                    "status": message.get("status") or "complete",
                },
            )
        )
    return events


def _synthetic_tool_event(
    source_event: StoredProtocolEvent,
    *,
    index: int,
    tool_call_id: str,
    event_name: str,
    data: dict[str, Any],
) -> StoredProtocolEvent:
    return stored_protocol_event(
        run_id=source_event["run_id"],
        thread_id=source_event["thread_id"],
        seq=source_event["seq"],
        method="tools",
        namespace=source_event["namespace"],
        event_id=f"{source_event['id']}:{event_name}:{tool_call_id}",
        id=f"{source_event['id']}:{event_name}:{index}:{tool_call_id}",
        data=data,
        timestamp=source_event["timestamp"],
    )


def _has_seen_tool_event(seen: set[str], *, kind: str, call_id: str) -> bool:
    return call_id in seen or _tool_event_seen_key(kind=kind, call_id=call_id) in seen


def _mark_seen_tool_event(seen: set[str], *, kind: str, call_id: str) -> None:
    seen.add(_tool_event_seen_key(kind=kind, call_id=call_id))


def _tool_event_seen_key(*, kind: str, call_id: str) -> str:
    return f"{kind}:{call_id}"


def _serialize_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_serialize_value(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _serialize_value(asdict(value))
    if hasattr(value, "model_dump"):
        return _serialize_value(value.model_dump())
    if hasattr(value, "dict"):
        return _serialize_value(value.dict())
    return repr(value)


def _coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
