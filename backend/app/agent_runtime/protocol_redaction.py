from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from app.agent_runtime.memory_event_projection import MEMORY_EVENT_NAMES, MEMORY_TOOL_NAMES
from app.marketplace.redaction import redact_keys

REDACTED_MEMORY_FIELD: Final = "<redacted>"


def redact_protocol_data(method: str, data: Any) -> Any:
    redacted = redact_keys(data)
    if method == "tools":
        return _redact_tool_event(redacted)
    if method == "custom":
        return _redact_custom_event(redacted)
    if method in {"values", "updates"}:
        return _redact_state_snapshot(redacted)
    return redacted


def _redact_tool_event(data: Any) -> Any:
    if not isinstance(data, Mapping):
        return data
    tool_name = _text(data.get("name") or data.get("tool_name") or data.get("tool"))
    if tool_name not in MEMORY_TOOL_NAMES:
        return data

    safe = dict(data)
    for args_key in ("args", "parameters"):
        args = safe.get(args_key)
        if isinstance(args, Mapping):
            safe[args_key] = _redact_memory_mapping(args)
    return safe


def _redact_custom_event(data: Any) -> Any:
    if not isinstance(data, Mapping):
        return data
    name = _text(data.get("name") or data.get("channel"))
    payload = data.get("payload")
    if name not in MEMORY_EVENT_NAMES or not isinstance(payload, Mapping):
        return data
    return {**dict(data), "payload": _redact_memory_mapping(payload)}


def _redact_state_snapshot(data: Any) -> Any:
    if isinstance(data, Mapping):
        return {str(key): _redact_state_snapshot(item) for key, item in data.items()}
    if isinstance(data, Sequence) and not isinstance(data, str | bytes | bytearray):
        return [_redact_state_snapshot(item) for item in data]
    return data


def _redact_memory_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    safe = dict(data)
    if "content" in safe:
        safe["content"] = REDACTED_MEMORY_FIELD
    if safe.get("reason") is not None:
        safe["reason"] = REDACTED_MEMORY_FIELD
    return safe


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""
