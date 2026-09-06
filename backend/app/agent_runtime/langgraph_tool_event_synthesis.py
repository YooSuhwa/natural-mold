from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_protocol_event
from app.agent_runtime.run_metrics_baseline import ToolCallSourceIdentity


def synthesize_tool_events_from_values(
    values_event: StoredProtocolEvent,
    *,
    seen_tool_call_ids: set[str] | None = None,
    baseline_completed_tool_call_source_identities: Collection[ToolCallSourceIdentity] = (),
    first_seq: int | None = None,
) -> list[StoredProtocolEvent]:
    if values_event["method"] != "values" or not isinstance(values_event["data"], Mapping):
        return []

    messages = values_event["data"].get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, str | bytes):
        return []

    seen = seen_tool_call_ids if seen_tool_call_ids is not None else set()
    synthesized: list[StoredProtocolEvent] = []
    pending_sources: dict[str, deque[ToolCallSourceIdentity]] = defaultdict(deque)
    next_seq = values_event["seq"] if first_seq is None else first_seq
    for index, message in enumerate(messages):
        normalized = _serialize_value(message)
        if not isinstance(normalized, Mapping):
            continue
        message_events = _synthesize_from_message(
            normalized,
            source_event=values_event,
            index=index,
            seen=seen,
            baseline_completed_tool_call_source_identities=(
                baseline_completed_tool_call_source_identities
            ),
            pending_sources=pending_sources,
            first_seq=next_seq,
        )
        synthesized.extend(message_events)
        next_seq += len(message_events)
    return synthesized


def _synthesize_from_message(
    message: Mapping[str, Any],
    *,
    source_event: StoredProtocolEvent,
    index: int,
    seen: set[str],
    baseline_completed_tool_call_source_identities: Collection[ToolCallSourceIdentity],
    pending_sources: dict[str, deque[ToolCallSourceIdentity]],
    first_seq: int,
) -> list[StoredProtocolEvent]:
    events: list[StoredProtocolEvent] = []
    next_seq = first_seq
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, Sequence) and not isinstance(tool_calls, str | bytes):
        for call in tool_calls:
            if not isinstance(call, Mapping):
                continue
            call_id = _coerce_optional_str(call.get("id"))
            source_message_id = _coerce_optional_str(message.get("id"))
            if source_message_id and call_id:
                source_identity = (tuple(source_event["namespace"]), source_message_id, call_id)
                pending_sources[call_id].append(source_identity)
            else:
                source_identity = None
            if (
                not call_id
                or source_identity in baseline_completed_tool_call_source_identities
                or _has_seen_tool_event(seen, kind="start", call_id=call_id)
            ):
                continue
            _mark_seen_tool_event(seen, kind="start", call_id=call_id)
            events.append(
                _synthetic_tool_event(
                    source_event,
                    index=index,
                    seq=next_seq,
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
            next_seq += 1

    tool_call_id = _coerce_optional_str(message.get("tool_call_id"))
    message_type = _coerce_optional_str(message.get("type"))
    source_identity = (
        pending_sources[tool_call_id].popleft()
        if tool_call_id and pending_sources[tool_call_id]
        else None
    )
    if (
        tool_call_id
        and source_identity not in baseline_completed_tool_call_source_identities
        and not _has_seen_tool_event(seen, kind="finish", call_id=tool_call_id)
        and message_type in {"tool", "ToolMessage"}
    ):
        _mark_seen_tool_event(seen, kind="finish", call_id=tool_call_id)
        events.append(
            _synthetic_tool_event(
                source_event,
                index=index,
                seq=next_seq,
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
    seq: int,
    tool_call_id: str,
    event_name: str,
    data: dict[str, Any],
) -> StoredProtocolEvent:
    return stored_protocol_event(
        run_id=source_event["run_id"],
        thread_id=source_event["thread_id"],
        seq=seq,
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
    dumped = _method_result(value, "model_dump")
    if dumped is not None:
        return _serialize_value(dumped)
    dict_value = _method_result(value, "dict")
    if dict_value is not None:
        return _serialize_value(dict_value)
    return repr(value)


def _method_result(value: Any, method_name: str) -> Any | None:
    method = getattr(value, method_name, None)
    return method() if callable(method) else None


def _coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
