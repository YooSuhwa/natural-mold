"""Compose internal offload projection with protocol secret redaction."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.agent_runtime.memory_event_projection import MEMORY_EVENT_NAMES
from app.agent_runtime.offload_protocol_projection import project_offload_egress_data
from app.agent_runtime.protocol_redaction import redact_memory_content, redact_protocol_data


def _memory_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.removeprefix("custom:").removeprefix("moldy.")
    if normalized in MEMORY_EVENT_NAMES or normalized == "memory_recalled":
        return normalized
    return None


def _redact_direct_memory_payload(method: str, data: Any) -> Any:
    name = _memory_name(method)
    if name is None or not isinstance(data, Mapping):
        return redact_memory_content(method, data)
    canonical_name = "moldy.memory_recalled" if name == "memory_recalled" else name
    wrapped_payload = data.get("payload")
    if isinstance(wrapped_payload, Mapping):
        envelope = redact_memory_content(
            "custom",
            {"name": canonical_name, "payload": dict(wrapped_payload)},
        )
        if not isinstance(envelope, Mapping):
            return data
        redacted_payload = envelope.get("payload", wrapped_payload)
        return {**dict(data), "payload": redacted_payload}
    envelope = redact_memory_content(
        "custom",
        {"name": canonical_name, "payload": dict(data)},
    )
    return envelope.get("payload", data) if isinstance(envelope, Mapping) else data


_EVENT_RECORD_COLLECTION_METHODS = frozenset(
    {"debug_traces", "protocol_replay", "share_traces", "traces"}
)


def _redact_nested_memory_events(
    data: Any,
    *,
    allow_event_records: bool = False,
) -> Any:
    if isinstance(data, Mapping):
        safe = {
            key: _redact_nested_memory_events(value, allow_event_records=False)
            for key, value in data.items()
        }

        method = safe.get("method")
        if isinstance(method, str) and _memory_name(method) is not None:
            if "data" in safe:
                safe["data"] = _redact_direct_memory_payload(method, safe["data"])
            params = safe.get("params")
            if isinstance(params, Mapping) and "data" in params:
                safe["params"] = {
                    **dict(params),
                    "data": _redact_direct_memory_payload(method, params["data"]),
                }

        if allow_event_records:
            seq = safe.get("seq")
            if isinstance(method, str) and isinstance(seq, int) and not isinstance(seq, bool):
                params = safe.get("params")
                if (
                    safe.get("type") == "event"
                    and isinstance(params, Mapping)
                    and isinstance(params.get("namespace"), list)
                    and "data" in params
                ):
                    safe["params"] = {
                        **dict(params),
                        "data": _redact_direct_memory_payload(method, params["data"]),
                    }
                is_stored_event = isinstance(safe.get("namespace"), list) and "data" in safe
                if is_stored_event:
                    safe["data"] = _redact_direct_memory_payload(method, safe["data"])

        event = safe.get("event")
        if isinstance(event, str) and "data" in safe:
            safe["data"] = _redact_direct_memory_payload(event, safe["data"])

        name = safe.get("name")
        payload = safe.get("payload")
        if _memory_name(name) is not None and isinstance(payload, Mapping):
            redacted = _redact_direct_memory_payload(
                str(name),
                {"payload": payload},
            )
            if isinstance(redacted, Mapping):
                return {**safe, "payload": redacted.get("payload", payload)}
            return safe

        value = safe.get("value")
        if _memory_name(name) is not None and isinstance(value, Mapping):
            nested_payload = value.get("payload")
            if isinstance(nested_payload, Mapping):
                safe["value"] = {
                    **dict(value),
                    "payload": _redact_direct_memory_payload(str(name), nested_payload),
                }
        return safe
    if isinstance(data, Sequence) and not isinstance(data, str | bytes | bytearray):
        return [
            _redact_nested_memory_events(item, allow_event_records=allow_event_records)
            for item in data
        ]
    return data


def project_and_redact_protocol_data(
    method: str,
    data: Any,
    *,
    secret_values: Iterable[str] | None = None,
    redact_memory: bool = True,
) -> Any:
    """Return a non-mutating public value with paths projected before redaction."""

    redacted = redact_protocol_data(
        method,
        project_offload_egress_data(data),
        redact_memory=False,
        secret_values=secret_values,
    )
    if not redact_memory:
        return redacted
    direct = _redact_direct_memory_payload(method, redacted)
    return _redact_nested_memory_events(
        direct,
        allow_event_records=method in _EVENT_RECORD_COLLECTION_METHODS,
    )


__all__ = ["project_and_redact_protocol_data"]
