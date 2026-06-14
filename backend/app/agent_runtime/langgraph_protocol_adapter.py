from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

from app.agent_runtime.langgraph_reasoning_redaction import redact_private_reasoning
from app.agent_runtime.langgraph_tool_event_synthesis import synthesize_tool_events_from_values
from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_protocol_event

__all__ = [
    "adapt_stream_mode_chunk",
    "adapt_v3_protocol_event",
    "extract_subagent_discovery",
    "synthesize_tool_events_from_values",
]

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
    raw_data = _merge_params_interrupts(
        data=params.get("data"),
        interrupts=params.get("interrupts"),
    )
    checkpoint_id, checkpoint_ns = _checkpoint_metadata(params, raw_data)
    data = _normalize_protocol_data(
        method,
        raw_data,
    )
    method, data = _normalize_method(method, data)
    data = redact_private_reasoning(data)

    return stored_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=_coerce_seq(raw_event.get("seq")),
        event_id=_coerce_optional_str(raw_event.get("event_id") or raw_event.get("id")),
        method=method,
        namespace=namespace,
        data=data,
        timestamp=_coerce_optional_str(params.get("timestamp")),
        checkpoint_id=checkpoint_id,
        checkpoint_ns=checkpoint_ns,
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
        data=redact_private_reasoning(normalized),
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


def _normalize_protocol_data(method: str, data: Any) -> Any:
    if method == "messages" and _is_payload_metadata_tuple(data):
        payload, metadata = data
        return [_serialize_value(payload), _serialize_value(metadata)]
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
    if method.startswith("custom:"):
        return "custom", {"name": method.removeprefix("custom:"), "payload": data}
    if method in RAW_PROTOCOL_METHODS:
        return method, data
    return "custom", {"name": method, "payload": data}


def _checkpoint_metadata(
    params: Mapping[str, Any],
    data: Any,
) -> tuple[str | None, str | None]:
    checkpoint_id = _coerce_optional_str(params.get("checkpoint_id"))
    checkpoint_ns = _coerce_optional_str(params.get("checkpoint_ns"))

    checkpoint = params.get("checkpoint")
    if isinstance(checkpoint, Mapping):
        checkpoint_id = checkpoint_id or _coerce_optional_str(checkpoint.get("checkpoint_id"))
        checkpoint_ns = checkpoint_ns or _coerce_optional_str(checkpoint.get("checkpoint_ns"))

    if _is_payload_metadata_tuple(data):
        metadata = data[1]
        checkpoint_id = checkpoint_id or _coerce_optional_str(metadata.get("checkpoint_id"))
        checkpoint_ns = checkpoint_ns or _coerce_optional_str(metadata.get("checkpoint_ns"))

    return checkpoint_id, checkpoint_ns


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

    interrupt = _serialize_interrupt_like(value)
    if interrupt is not None:
        return interrupt

    message = _serialize_message_like(value)
    if message is not None:
        return message

    return repr(value)


def _method_result(value: Any, method_name: str) -> Any | None:
    method = getattr(value, method_name, None)
    return method() if callable(method) else None


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
