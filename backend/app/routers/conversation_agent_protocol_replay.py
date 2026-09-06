from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Collection, Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    SubscribeParams,
    format_protocol_sse,
    matches_subscription,
    protocol_event_cursor,
    resequence_protocol_events,
    stored_custom_protocol_event,
)
from app.models.conversation import Conversation
from app.models.message_event import MessageEvent
from app.routers.conversation_agent_protocol_event_normalization import (
    stored_compatible_protocol_event,
)
from app.routers.conversation_agent_protocol_legacy import protocol_events_from_legacy_sse
from app.services import trace_storage
from app.services.chat import secrets as chat_secrets


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


def _stored_events_from_raw(
    raw: Mapping[str, Any],
    *,
    record: MessageEvent,
    fallback_seq: int,
    secret_values: Collection[str] | None = None,
) -> list[StoredProtocolEvent]:
    projected_raw = project_and_redact_protocol_data(
        "protocol_replay",
        raw,
        secret_values=secret_values,
    )
    if not isinstance(projected_raw, Mapping):
        return []

    method = _optional_str(projected_raw.get("method"))
    seq = _int_value(projected_raw.get("seq"))
    if method is not None and seq is not None and "data" in projected_raw:
        return [
            stored_compatible_protocol_event(
                run_id=_optional_str(projected_raw.get("run_id")) or record.assistant_msg_id,
                thread_id=_optional_str(projected_raw.get("thread_id"))
                or str(record.conversation_id),
                seq=seq,
                method=method,
                namespace=_namespace(projected_raw.get("namespace")),
                data=projected_raw.get("data"),
                event_id=_optional_str(projected_raw.get("upstream_event_id")),
                timestamp=_optional_str(projected_raw.get("timestamp")),
                id=_optional_str(projected_raw.get("id")),
                checkpoint_id=_optional_str(projected_raw.get("checkpoint_id")),
                checkpoint_ns=_optional_str(projected_raw.get("checkpoint_ns")),
            )
        ]

    if projected_raw.get("type") != "event":
        return protocol_events_from_legacy_sse(
            projected_raw,
            record=record,
            seq=_legacy_seq(projected_raw, fallback=fallback_seq),
        )
    params = projected_raw.get("params")
    if not isinstance(params, Mapping):
        return []
    wire_method = _optional_str(projected_raw.get("method"))
    wire_seq = _int_value(projected_raw.get("seq"))
    if wire_method is None or wire_seq is None:
        return []
    return [
        stored_compatible_protocol_event(
            run_id=record.assistant_msg_id,
            thread_id=str(record.conversation_id),
            seq=wire_seq,
            method=wire_method,
            namespace=_namespace(params.get("namespace")),
            data=params.get("data"),
            event_id=_optional_str(projected_raw.get("event_id")),
            timestamp=_optional_str(params.get("timestamp")),
            checkpoint_id=_optional_str(params.get("checkpoint_id")),
            checkpoint_ns=_optional_str(params.get("checkpoint_ns")),
        )
    ]


def _legacy_seq(raw: Mapping[str, Any], *, fallback: int) -> int:
    event_id = _optional_str(raw.get("id"))
    if event_id is None:
        return fallback
    tail = event_id.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else fallback


def _event_matches_cursor(event: StoredProtocolEvent, cursor: str) -> bool:
    return cursor in {
        event["id"],
        event["upstream_event_id"],
        protocol_event_cursor(event),
        str(event["seq"]),
    }


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
    return stored_custom_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=seq,
        name="stale",
        payload={
            "reason": "run_worker_lost",
            "run_id": run_id,
            "last_event_id": last_event_id,
        },
        event_id=f"{run_id}:stale",
    )


async def load_protocol_events(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    secret_values: Collection[str] | None = None,
) -> list[StoredProtocolEvent]:
    if secret_values is None:
        conversation = await db.get(Conversation, conversation_id)
        secret_values = (
            tuple(await chat_secrets.collect_conversation_secret_values(db, conversation))
            if conversation is not None
            else ()
        )
    records = await trace_storage.get_traces_for_conversation(db, conversation_id)
    events: list[StoredProtocolEvent] = []
    for record in records:
        for index, raw in enumerate(await trace_storage.load_events(db, record), start=1):
            if not isinstance(raw, Mapping):
                continue
            events.extend(
                _stored_events_from_raw(
                    raw,
                    record=record,
                    fallback_seq=index,
                    secret_values=secret_values,
                )
            )
    return resequence_protocol_events(events)


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
