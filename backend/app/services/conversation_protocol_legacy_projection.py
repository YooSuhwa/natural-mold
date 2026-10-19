from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.agent_runtime import event_names
from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    stored_custom_protocol_event,
    stored_protocol_event,
)
from app.models.message_event import MessageEvent
from app.schemas.conversation_history import JsonValue


@dataclass(frozen=True, slots=True)
class LegacyEventProjectionRequest:
    raw: Mapping[str, JsonValue]
    record: MessageEvent
    seq: int


@dataclass(frozen=True, slots=True)
class LegacyStoredEventRequest:
    record: MessageEvent
    seq: int
    method: str
    data: JsonValue
    event_id: str | None


def protocol_events_from_legacy_sse(
    request: LegacyEventProjectionRequest,
) -> list[StoredProtocolEvent]:
    raw = request.raw
    data = _mapping(raw.get("data"))
    raw_id = _optional_str(raw.get("id"))
    if raw.get("event") == event_names.MESSAGE_END:
        return [
            _legacy_protocol_event(
                LegacyStoredEventRequest(
                    request.record,
                    request.seq,
                    "lifecycle",
                    _terminal_lifecycle_data(request.record, data),
                    _derived_event_id(raw_id, "lifecycle"),
                )
            )
        ]
    if raw.get("event") == event_names.STALE:
        stale_data: dict[str, JsonValue] = {
            **data,
            "event": event_names.STALE,
            "status": "error",
            "run_id": data.get("run_id") or request.record.assistant_msg_id,
        }
        custom_data: dict[str, JsonValue] = {
            **data,
            "run_id": data.get("run_id") or request.record.assistant_msg_id,
        }
        return [
            _legacy_protocol_event(
                LegacyStoredEventRequest(
                    request.record,
                    request.seq,
                    "lifecycle",
                    stale_data,
                    _derived_event_id(raw_id, "lifecycle"),
                )
            ),
            _legacy_custom_protocol_event(
                LegacyStoredEventRequest(
                    request.record,
                    request.seq,
                    event_names.STALE,
                    custom_data,
                    _derived_event_id(raw_id, event_names.STALE),
                )
            ),
        ]
    if raw.get("event") == event_names.ERROR:
        return [
            _legacy_protocol_event(
                LegacyStoredEventRequest(
                    request.record,
                    request.seq,
                    "error",
                    data,
                    _derived_event_id(raw_id, "error"),
                )
            )
        ]
    return []


def _terminal_lifecycle_data(
    record: MessageEvent, data: Mapping[str, JsonValue]
) -> dict[str, JsonValue]:
    status = _optional_str(data.get("status")) or "completed"
    return {**data, "event": status, "status": status, "run_id": record.assistant_msg_id}


def _legacy_protocol_event(request: LegacyStoredEventRequest) -> StoredProtocolEvent:
    return stored_protocol_event(
        run_id=request.record.assistant_msg_id,
        thread_id=str(request.record.conversation_id),
        seq=request.seq,
        method=request.method,
        data=request.data,
        event_id=request.event_id,
        id=request.event_id,
    )


def _legacy_custom_protocol_event(request: LegacyStoredEventRequest) -> StoredProtocolEvent:
    return stored_custom_protocol_event(
        run_id=request.record.assistant_msg_id,
        thread_id=str(request.record.conversation_id),
        seq=request.seq,
        name=request.method,
        payload=request.data,
        event_id=request.event_id,
        id=request.event_id,
    )


def _mapping(value: JsonValue) -> dict[str, JsonValue]:
    return dict(value) if isinstance(value, Mapping) else {}


def _optional_str(value: JsonValue) -> str | None:
    return value if isinstance(value, str) and value else None


def _derived_event_id(raw_id: str | None, suffix: str) -> str | None:
    return f"{raw_id}:{suffix}" if raw_id else None


__all__ = ["LegacyEventProjectionRequest", "protocol_events_from_legacy_sse"]
