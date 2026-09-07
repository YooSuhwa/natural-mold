from __future__ import annotations

import json
import math

from fastapi import HTTPException

from app.models.conversation_run_input import JsonValue

MAX_QUEUE_INPUT_BYTES = 256 * 1024
MAX_QUEUE_INPUT_DEPTH = 20


def _validate_value(value: JsonValue, *, depth: int) -> None:
    if depth > MAX_QUEUE_INPUT_DEPTH:
        raise HTTPException(status_code=422, detail="Queued input exceeds maximum nesting depth")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise HTTPException(status_code=422, detail="Queued input must contain finite numbers")
        return
    if isinstance(value, list):
        for item in value:
            _validate_value(item, depth=depth + 1)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _validate_value(item, depth=depth + 1)
        return
    raise HTTPException(status_code=422, detail="Queued input must be JSON-compatible")


def validate_queue_input_payload(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    _validate_value(value, depth=1)
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > MAX_QUEUE_INPUT_BYTES:
        raise HTTPException(status_code=422, detail="Queued input exceeds maximum size")
    return value


__all__ = [
    "JsonValue",
    "MAX_QUEUE_INPUT_BYTES",
    "MAX_QUEUE_INPUT_DEPTH",
    "validate_queue_input_payload",
]
