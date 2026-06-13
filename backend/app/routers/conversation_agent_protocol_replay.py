from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    SubscribeParams,
    format_protocol_sse,
    matches_subscription,
    stored_protocol_event,
)
from app.models.message_event import MessageEvent
from app.services import trace_storage


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_value(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return int(value) if isinstance(value, str) and value.isdigit() else None


def _namespace(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [segment for segment in value if isinstance(segment, str)]


def _stored_event_from_raw(
    raw: Mapping[str, Any],
    *,
    record: MessageEvent,
) -> StoredProtocolEvent | None:
    method = _optional_str(raw.get("method"))
    seq = _int_value(raw.get("seq"))
    if method is not None and seq is not None and "data" in raw:
        return stored_protocol_event(
            run_id=_optional_str(raw.get("run_id")) or record.assistant_msg_id,
            thread_id=_optional_str(raw.get("thread_id")) or str(record.conversation_id),
            seq=seq,
            method=method,
            namespace=_namespace(raw.get("namespace")),
            data=raw.get("data"),
            event_id=_optional_str(raw.get("upstream_event_id")),
            timestamp=_optional_str(raw.get("timestamp")),
            id=_optional_str(raw.get("id")),
        )

    if raw.get("type") != "event":
        return None
    params = raw.get("params")
    if not isinstance(params, Mapping):
        return None
    wire_method = _optional_str(raw.get("method"))
    wire_seq = _int_value(raw.get("seq"))
    if wire_method is None or wire_seq is None:
        return None
    return stored_protocol_event(
        run_id=record.assistant_msg_id,
        thread_id=str(record.conversation_id),
        seq=wire_seq,
        method=wire_method,
        namespace=_namespace(params.get("namespace")),
        data=params.get("data"),
        event_id=_optional_str(raw.get("event_id")),
        timestamp=_optional_str(params.get("timestamp")),
    )


def _event_matches_cursor(event: StoredProtocolEvent, cursor: str) -> bool:
    return cursor in {event["id"], event["upstream_event_id"], str(event["seq"])}


def _events_after_cursor(
    events: Sequence[StoredProtocolEvent],
    after_id: str | None,
) -> Sequence[StoredProtocolEvent]:
    if not after_id:
        return events
    for index, event in enumerate(events):
        if _event_matches_cursor(event, after_id):
            return events[index + 1 :]
    return events


def protocol_stale_event(
    *,
    run_id: str,
    thread_id: str,
    seq: int,
    last_event_id: str | None,
) -> StoredProtocolEvent:
    return stored_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=seq,
        method="custom:stale",
        data={
            "name": "stale",
            "reason": "run_worker_lost",
            "run_id": run_id,
            "last_event_id": last_event_id,
        },
        event_id=f"{run_id}:stale",
    )


async def load_protocol_events(
    db: AsyncSession,
    conversation_id: uuid.UUID,
) -> list[StoredProtocolEvent]:
    records = await trace_storage.get_traces_for_conversation(db, conversation_id)
    events: list[StoredProtocolEvent] = []
    for record in records:
        for raw in record.events or []:
            if not isinstance(raw, Mapping):
                continue
            event = _stored_event_from_raw(raw, record=record)
            if event is not None:
                events.append(event)
    return events


async def protocol_replay_generator(
    events: Sequence[StoredProtocolEvent],
    params: SubscribeParams,
    *,
    after_id: str | None,
    final_events: Sequence[StoredProtocolEvent] | None = None,
) -> AsyncGenerator[str, None]:
    for event in _events_after_cursor(events, after_id):
        if matches_subscription(event, params):
            yield format_protocol_sse(event)
    for event in final_events or []:
        if matches_subscription(event, params):
            yield format_protocol_sse(event)
