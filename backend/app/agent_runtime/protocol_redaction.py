from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from app.agent_runtime.memory_event_projection import MEMORY_EVENT_NAMES, MEMORY_TOOL_NAMES
from app.marketplace.redaction import is_sensitive_key

REDACTED_MEMORY_FIELD: Final = "<redacted>"
REDACTED_SENSITIVE_FIELD: Final = "<redacted>"
SAFE_TOKEN_METRIC_KEYS: Final = frozenset(
    {
        "cache_creation_tokens",
        "cache_read_tokens",
        "completion_tokens",
        "estimated_cost",
        "input_token_details",
        "input_tokens",
        "output_token_details",
        "output_tokens",
        "prompt_tokens",
        "total_tokens",
        "usage",
        "usage_metadata",
    }
)
SENSITIVE_KEY_SOURCE: Final = (
    r"password|api[_-]?key|secret|token|access[_-]?key|refresh[_-]?token|"
    r"client[_-]?secret|private[_-]?key"
)
SENSITIVE_ASSIGNMENT_RE: Final = re.compile(
    rf"((?:{SENSITIVE_KEY_SOURCE})[\"']?\s*[:=]\s*[\"']?)([^\"',}}\]\s]+)([\"']?)",
    re.IGNORECASE,
)
ASSIGNMENT_KEY_RE: Final = re.compile(r"([A-Za-z0-9_-]+)")


def redact_protocol_data(method: str, data: Any, *, redact_memory: bool = True) -> Any:
    redacted = _redact_sensitive_keys(data)
    if redact_memory and method == "tools":
        return _redact_tool_event(redacted)
    if redact_memory and method == "custom":
        return _redact_custom_event(redacted)
    if method in {"values", "updates"}:
        return _redact_state_snapshot(redacted)
    return redacted


def _redact_sensitive_keys(data: Any) -> Any:
    if isinstance(data, Mapping):
        return {
            str(key): (
                REDACTED_SENSITIVE_FIELD
                if _is_sensitive_protocol_key(str(key))
                else _redact_sensitive_keys(value)
            )
            for key, value in data.items()
        }
    if isinstance(data, Sequence) and not isinstance(data, str | bytes | bytearray):
        return [_redact_sensitive_keys(item) for item in data]
    if isinstance(data, str):
        return _redact_sensitive_string(data)
    return data


def _is_sensitive_protocol_key(key: str) -> bool:
    return key not in SAFE_TOKEN_METRIC_KEYS and is_sensitive_key(key)


def _redact_sensitive_string(data: str) -> str:
    parsed = _parse_json_container(data)
    if parsed is not None:
        redacted = _redact_sensitive_keys(parsed)
        if redacted != parsed:
            return json.dumps(redacted, ensure_ascii=False, separators=(",", ":"))
    return SENSITIVE_ASSIGNMENT_RE.sub(_redact_assignment_match, data)


def _parse_json_container(data: str) -> Any | None:
    stripped = data.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        parsed = json.loads(data)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, Mapping | Sequence) else None


def _redact_assignment_match(match: re.Match[str]) -> str:
    prefix = match.group(1)
    key_match = ASSIGNMENT_KEY_RE.match(prefix)
    if key_match and not _is_sensitive_protocol_key(key_match.group(1)):
        return match.group(0)
    return f"{prefix}{REDACTED_SENSITIVE_FIELD}{match.group(3)}"


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
