from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

PRIVATE_REASONING_KEYS: Final = frozenset(
    {
        "chain_of_thought",
        "cot",
        "reasoning",
        "reasoning_content",
        "thinking",
        "thinking_content",
        "private_reasoning",
    }
)
REASONING_BLOCK_TYPES: Final = frozenset(
    {
        "chain_of_thought",
        "cot",
        "reasoning",
        "reasoning_content",
        "redacted_thinking",
        "thinking",
        "thinking_content",
    }
)
DISPLAYABLE_REASONING_KEYS: Final = frozenset(
    {"type", "id", "index", "summary", "message", "status", "signature"}
)


def redact_private_reasoning(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _redact_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [redact_private_reasoning(item) for item in value]
    return value


def _redact_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    block_type = _normalized_key(value.get("type"))
    if _is_reasoning_block_type(block_type):
        return _displayable_reasoning_summary(value)

    result: dict[str, Any] = {}
    for raw_key, item in value.items():
        key = str(raw_key)
        if _is_private_reasoning_key(_normalized_key(key)):
            result[key] = "[redacted]"
        else:
            result[key] = redact_private_reasoning(item)
    return result


def _displayable_reasoning_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        str(key): redact_private_reasoning(item)
        for key, item in value.items()
        if str(key) in DISPLAYABLE_REASONING_KEYS
    }
    if len(result) != len(value):
        result["redacted"] = True
    return result


def _is_private_reasoning_key(key: str) -> bool:
    return key in PRIVATE_REASONING_KEYS or key.endswith("_reasoning")


def _is_reasoning_block_type(value: str) -> bool:
    return value in REASONING_BLOCK_TYPES


def _normalized_key(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower().replace("-", "_")
