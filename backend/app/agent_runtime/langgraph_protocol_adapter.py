from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_protocol_event

RAW_PROTOCOL_METHODS = {
    "values",
    "updates",
    "messages",
    "tools",
    "lifecycle",
    "input",
    "input.requested",
    "checkpoints",
    "tasks",
    "custom",
    "metadata",
    "error",
    "subagents",
    "subgraphs",
    "tool_calls",
}


def adapt_v3_protocol_event(
    raw_event: Mapping[str, Any],
    *,
    run_id: str,
    thread_id: str,
) -> StoredProtocolEvent:
    method = _coerce_str(raw_event.get("method"), default="custom")
    params = raw_event.get("params")
    if not isinstance(params, Mapping):
        params = {}

    namespace = _coerce_namespace(params.get("namespace"))
    data = _normalize_protocol_data(
        method,
        _merge_params_interrupts(
            data=params.get("data"),
            interrupts=params.get("interrupts"),
        ),
    )
    method, data = _normalize_method(method, data)

    return stored_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=_coerce_seq(raw_event.get("seq")),
        event_id=_coerce_optional_str(raw_event.get("event_id") or raw_event.get("id")),
        method=method,
        namespace=namespace,
        data=data,
        timestamp=_coerce_optional_str(params.get("timestamp")),
    )


def adapt_stream_mode_chunk(
    chunk: Sequence[Any],
    *,
    run_id: str,
    thread_id: str,
    seq: int,
    event_id: str | None = None,
) -> StoredProtocolEvent:
    if len(chunk) == 2:
        namespace: list[str] = []
        method, data = chunk
    elif len(chunk) == 3:
        namespace = _coerce_namespace(chunk[0])
        method, data = chunk[1], chunk[2]
    else:
        raise ValueError("stream-mode chunks must be (mode, data) or (namespace, mode, data)")

    method_name, normalized = _normalize_method(
        _coerce_str(method, default="custom"),
        _normalize_protocol_data(_coerce_str(method, default="custom"), data),
    )
    return stored_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=seq,
        event_id=event_id,
        method=method_name,
        namespace=namespace,
        data=normalized,
    )


def extract_subagent_discovery(event: StoredProtocolEvent) -> dict[str, Any] | None:
    if event["method"] not in {"tasks", "subagents", "subgraphs", "lifecycle"}:
        return None
    if not isinstance(event["data"], Mapping):
        return None

    data = event["data"]
    name = data.get("name") or data.get("agent_name") or data.get("graph_name")
    path = data.get("path") or event["namespace"]
    status = data.get("status") or data.get("state")
    cause = data.get("cause") if isinstance(data.get("cause"), Mapping) else None
    trigger_call_id = (
        data.get("trigger_call_id")
        or data.get("tool_call_id")
        or (cause.get("tool_call_id") if cause else None)
    )

    if not name and not trigger_call_id and not path:
        return None

    return {
        "id": _coerce_optional_str(data.get("id") or trigger_call_id or event["id"]),
        "name": _coerce_optional_str(name) or "subagent",
        "path": _serialize_value(path),
        "status": _normalize_status(_coerce_optional_str(status)),
        "trigger_call_id": _coerce_optional_str(trigger_call_id),
        "cause": _serialize_value(cause) if cause else None,
        "task_input": _serialize_value(data.get("task_input") or data.get("input")),
        "output": _serialize_value(data.get("output")),
        "error": _serialize_value(data.get("error")),
    }


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
            if not call_id or call_id in seen:
                continue
            seen.add(call_id)
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
    if tool_call_id and tool_call_id not in seen and message_type in {"tool", "ToolMessage"}:
        seen.add(tool_call_id)
        events.append(
            _synthetic_tool_event(
                source_event,
                index=index,
                tool_call_id=tool_call_id,
                event_name="tool-finished",
                data={
                    "event": "tool-finished",
                    "tool_call_id": tool_call_id,
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


def _normalize_protocol_data(method: str, data: Any) -> Any:
    if method in {"messages", "tools"} and _is_payload_metadata_tuple(data):
        payload, metadata = data
        normalized_payload = _serialize_value(payload)
        normalized_metadata = _serialize_value(metadata)
        if isinstance(normalized_payload, dict):
            return {**normalized_payload, "metadata": normalized_metadata}
        return {"payload": normalized_payload, "metadata": normalized_metadata}
    return _serialize_value(data)


def _merge_params_interrupts(*, data: Any, interrupts: Any) -> Any:
    if interrupts is None:
        return data
    if isinstance(data, Mapping):
        if "__interrupt__" in data:
            return data
        return {**data, "__interrupt__": interrupts}
    if data is None:
        return {"__interrupt__": interrupts}
    return {"payload": data, "__interrupt__": interrupts}


def _normalize_method(method: str, data: Any) -> tuple[str, Any]:
    if method in RAW_PROTOCOL_METHODS or method.startswith("custom:"):
        return method, data
    return "custom", {"name": method, "payload": data}


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

    interrupt = _serialize_interrupt_like(value)
    if interrupt is not None:
        return interrupt

    message = _serialize_message_like(value)
    if message is not None:
        return message

    return repr(value)


def _serialize_interrupt_like(value: Any) -> dict[str, Any] | None:
    if value.__class__.__name__ != "Interrupt":
        return None
    interrupt_id = getattr(value, "id", None)
    if not isinstance(interrupt_id, str) or not interrupt_id:
        return None
    return {"value": _serialize_value(getattr(value, "value", None)), "id": interrupt_id}


def _serialize_message_like(value: Any) -> dict[str, Any] | None:
    fields = {
        "id": getattr(value, "id", None),
        "type": getattr(value, "type", None) or value.__class__.__name__,
        "content": getattr(value, "content", None),
        "name": getattr(value, "name", None),
        "tool_call_id": getattr(value, "tool_call_id", None),
        "tool_calls": getattr(value, "tool_calls", None),
        "tool_call_chunks": getattr(value, "tool_call_chunks", None),
        "additional_kwargs": getattr(value, "additional_kwargs", None),
        "response_metadata": getattr(value, "response_metadata", None),
        "usage_metadata": getattr(value, "usage_metadata", None),
    }
    if all(item is None for item in fields.values()):
        return None
    return {key: _serialize_value(item) for key, item in fields.items() if item is not None}


def _is_payload_metadata_tuple(value: Any) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, str | bytes | bytearray)
        and len(value) == 2
        and isinstance(value[1], Mapping)
    )


def _coerce_namespace(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [str(segment) for segment in value]
    raise TypeError("namespace must be a string or sequence of strings")


def _coerce_seq(value: Any) -> int:
    if value is None:
        return 0
    try:
        seq = int(value)
    except (TypeError, ValueError) as exc:
        raise TypeError("seq must be an integer") from exc
    if seq < 0:
        raise ValueError("seq must be non-negative")
    return seq


def _coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _coerce_str(value: Any, *, default: str) -> str:
    if value is None:
        return default
    return str(value)


def _normalize_status(status: str | None) -> str:
    return {
        "started": "running",
        "running": "running",
        "completed": "complete",
        "complete": "complete",
        "success": "complete",
        "failed": "error",
        "error": "error",
        "interrupted": "requires_action",
        "cancelled": "cancelled",
        "canceled": "cancelled",
    }.get(status or "", status or "running")
