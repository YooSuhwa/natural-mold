from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from pydantic import TypeAdapter

from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    stored_custom_protocol_event,
    stored_protocol_event,
)
from app.models.message_event import MessageEvent
from app.schemas.conversation_history import JsonValue
from app.services.conversation_protocol_legacy_projection import (
    LegacyEventProjectionRequest,
    protocol_events_from_legacy_sse,
)

_JSON = TypeAdapter(JsonValue)


@dataclass(frozen=True, slots=True)
class CompatibleEventRequest:
    run_id: str
    thread_id: str
    seq: int
    method: str
    namespace: list[str] | None = None
    data: JsonValue = None
    event_id: str | None = None
    timestamp: str | None = None
    id: str | None = None
    checkpoint_id: str | None = None
    checkpoint_ns: str | None = None


@dataclass(frozen=True, slots=True)
class RawEventProjectionRequest:
    raw: Mapping[str, JsonValue]
    record: MessageEvent
    fallback_seq: int
    secret_values: Collection[str] | None = None


def stored_compatible_protocol_event(
    request: CompatibleEventRequest,
) -> StoredProtocolEvent:
    if request.method.startswith("custom:"):
        name = request.method.removeprefix("custom:")
        if name:
            return stored_custom_protocol_event(
                run_id=request.run_id,
                thread_id=request.thread_id,
                seq=request.seq,
                name=name,
                payload=_custom_payload(request.data, name),
                namespace=request.namespace,
                event_id=request.event_id,
                timestamp=request.timestamp,
                id=request.id,
                checkpoint_id=request.checkpoint_id,
                checkpoint_ns=request.checkpoint_ns,
            )
    return stored_protocol_event(
        run_id=request.run_id,
        thread_id=request.thread_id,
        seq=request.seq,
        method=request.method,
        namespace=request.namespace,
        data=request.data,
        event_id=request.event_id,
        timestamp=request.timestamp,
        id=request.id,
        checkpoint_id=request.checkpoint_id,
        checkpoint_ns=request.checkpoint_ns,
    )


def stored_events_from_raw(
    request: RawEventProjectionRequest,
) -> list[StoredProtocolEvent]:
    projected = _JSON.validate_python(
        project_and_redact_protocol_data(
            "protocol_replay",
            request.raw,
            secret_values=request.secret_values,
        ),
        strict=True,
    )
    if not isinstance(projected, Mapping):
        return []
    method = _optional_str(projected.get("method"))
    seq = _optional_int(projected.get("seq"))
    if method is not None and seq is not None and "data" in projected:
        return [
            stored_compatible_protocol_event(
                CompatibleEventRequest(
                    run_id=_optional_str(projected.get("run_id"))
                    or request.record.assistant_msg_id,
                    thread_id=_optional_str(projected.get("thread_id"))
                    or str(request.record.conversation_id),
                    seq=seq,
                    method=method,
                    namespace=_namespace(projected.get("namespace")),
                    data=projected.get("data"),
                    event_id=_optional_str(projected.get("upstream_event_id")),
                    timestamp=_optional_str(projected.get("timestamp")),
                    id=_optional_str(projected.get("id")),
                    checkpoint_id=_optional_str(projected.get("checkpoint_id")),
                    checkpoint_ns=_optional_str(projected.get("checkpoint_ns")),
                )
            )
        ]
    if projected.get("type") != "event":
        return protocol_events_from_legacy_sse(
            LegacyEventProjectionRequest(
                projected,
                request.record,
                _legacy_seq(projected, request.fallback_seq),
            )
        )
    params = projected.get("params")
    if not isinstance(params, Mapping):
        return []
    wire_method = _optional_str(projected.get("method"))
    wire_seq = _optional_int(projected.get("seq"))
    if wire_method is None or wire_seq is None:
        return []
    return [
        stored_compatible_protocol_event(
            CompatibleEventRequest(
                run_id=request.record.assistant_msg_id,
                thread_id=str(request.record.conversation_id),
                seq=wire_seq,
                method=wire_method,
                namespace=_namespace(params.get("namespace")),
                data=params.get("data"),
                event_id=_optional_str(projected.get("event_id")),
                timestamp=_optional_str(params.get("timestamp")),
                checkpoint_id=_optional_str(params.get("checkpoint_id")),
                checkpoint_ns=_optional_str(params.get("checkpoint_ns")),
            )
        )
    ]


def _custom_payload(data: JsonValue, name: str) -> JsonValue:
    if not isinstance(data, Mapping):
        return data
    custom_name = f"custom:{name}"
    if data.get("name") in {name, custom_name} and "payload" in data:
        return data.get("payload")
    if data.get("channel") in {name, custom_name} and "payload" in data:
        return data.get("payload")
    return dict(data)


def _optional_str(value: JsonValue) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: JsonValue) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return int(value) if isinstance(value, str) and value.isdigit() else None


def _namespace(value: JsonValue) -> list[str]:
    if not isinstance(value, list):
        return []
    return [segment for segment in value if isinstance(segment, str)]


def _legacy_seq(raw: Mapping[str, JsonValue], fallback: int) -> int:
    event_id = _optional_str(raw.get("id"))
    if event_id is None:
        return fallback
    tail = event_id.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else fallback


__all__ = [
    "CompatibleEventRequest",
    "RawEventProjectionRequest",
    "stored_compatible_protocol_event",
    "stored_events_from_raw",
]
