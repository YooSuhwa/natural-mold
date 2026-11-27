"""Hash submitted JSON without applying the SDK's event-output serialization rules."""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel

from app.exceptions import ValidationError
from app.models.conversation_run_input import JsonValue


def _json_value(value: object) -> JsonValue:
    if isinstance(value, BaseModel):
        # The SDK intentionally elides optional nulls on output. Read only fields
        # the caller actually supplied, including explicit nulls and extensions.
        explicit = value.model_fields_set
        members = {
            field.serialization_alias or name: getattr(value, name)
            for name, field in type(value).model_fields.items()
            if name in explicit
        }
        members.update(value.model_extra or {})
        return _json_value(members)
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("Invalid JSON object key")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError("Invalid JSON value")


def ag_ui_request_digest(value: object) -> str:
    try:
        encoded = json.dumps(
            _json_value(value),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            ensure_ascii=False,
        ).encode("utf-8")
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValidationError("AG_UI_INVALID_INPUT", "Invalid AG-UI JSON input") from exc
    return hashlib.sha256(encoded).hexdigest()
