from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from langchain_core.messages import BaseMessage
from pydantic import TypeAdapter, ValidationError

from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.schemas.conversation_history import JsonValue
from app.services.conversation_protocol_projection import (
    MessageProjectionRequest,
    serialize_checkpoint_value,
    serialize_langchain_message,
)

_JSON = TypeAdapter(JsonValue)
_FORBIDDEN_CHANNELS: Final = frozenset(
    {
        "agent_config",
        "credentials",
        "mcp_transport_headers",
        "provider_api_keys",
        "tool_definitions",
        "tools_config",
    }
)


@dataclass(frozen=True, slots=True)
class SanitizedCheckpointPayload:
    channel_values: dict[str, JsonValue]
    messages: list[JsonValue]


class UnsupportedHistoryValueError(RuntimeError):
    """A checkpoint or event could not cross the retained JSON boundary."""


def sanitize_checkpoint_payload(
    values: Mapping[str, JsonValue],
    messages: Sequence[BaseMessage],
    secrets: Collection[str],
) -> SanitizedCheckpointPayload:
    """Serialize supported values and apply the established egress redactor."""
    forbidden = _FORBIDDEN_CHANNELS.intersection(values)
    if forbidden:
        raise UnsupportedHistoryValueError("forbidden executable checkpoint channel")
    serialized = {
        key: serialize_checkpoint_value(value) for key, value in values.items() if key != "messages"
    }
    projected = project_and_redact_protocol_data(
        "values",
        serialized,
        secret_values=secrets,
    )
    safe_values = _parse_mapping(projected)
    safe_messages = [
        _parse_json(
            serialize_langchain_message(MessageProjectionRequest(message, secret_values=secrets)),
        )
        for message in messages
    ]
    payload = SanitizedCheckpointPayload(safe_values, safe_messages)
    _reject_secret_bytes(
        {"channel_values": payload.channel_values, "messages": payload.messages},
        secrets,
    )
    return payload


def sanitize_event_payloads(
    events: Sequence[Mapping[str, JsonValue]],
    secrets: Collection[str],
) -> list[JsonValue]:
    """Project already-persisted legacy events without invoking wire compaction."""
    projected = project_and_redact_protocol_data(
        "protocol_replay",
        list(events),
        secret_values=secrets,
    )
    parsed = _parse_json(projected)
    if not isinstance(parsed, list):
        raise UnsupportedHistoryValueError("event projection is not a list")
    _reject_secret_bytes(parsed, secrets)
    return parsed


def canonical_payload_sha256(value: JsonValue) -> str:
    """Hash canonical UTF-8 JSON for stable immutable manifests."""
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _parse_mapping(value: JsonValue) -> dict[str, JsonValue]:
    parsed = _parse_json(value)
    if not isinstance(parsed, dict):
        raise UnsupportedHistoryValueError("checkpoint projection is not an object")
    return parsed


def _parse_json(value: JsonValue) -> JsonValue:
    try:
        return _JSON.validate_python(value, strict=True)
    except ValidationError as error:
        raise UnsupportedHistoryValueError("unsupported retained JSON value") from error


def _canonical_bytes(value: JsonValue) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError) as error:
        raise UnsupportedHistoryValueError("retained value is not canonical JSON") from error


def _reject_secret_bytes(value: JsonValue, secrets: Collection[str]) -> None:
    encoded = _canonical_bytes(value)
    if any(secret.encode() in encoded for secret in secrets):
        raise UnsupportedHistoryValueError("secret remained after history projection")


__all__ = [
    "SanitizedCheckpointPayload",
    "UnsupportedHistoryValueError",
    "canonical_payload_sha256",
    "sanitize_checkpoint_payload",
    "sanitize_event_payloads",
]
