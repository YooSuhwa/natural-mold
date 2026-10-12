from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Collection, Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    SubscribeParams,
    format_protocol_sse,
    matches_subscription,
    protocol_event_cursor,
    resequence_protocol_events,
    stored_custom_protocol_event,
)
from app.authz.conversation_scope import load_private_conversation
from app.authz.org_context import OrgContext
from app.error_codes import conversation_not_found
from app.models.message_event import MessageEvent
from app.services.conversation_protocol_event_projection import (
    RawEventProjectionRequest,
    stored_events_from_raw,
)
from app.services.conversation_protocol_replay import (
    LiveProtocolEventsRequest,
    load_live_protocol_events,
    parse_stored_event_mapping,
)


def _stored_events_from_raw(
    raw: Mapping[str, Any],
    *,
    record: MessageEvent,
    fallback_seq: int,
    secret_values: Collection[str] | None = None,
) -> list[StoredProtocolEvent]:
    return stored_events_from_raw(
        RawEventProjectionRequest(
            parse_stored_event_mapping(raw),
            record,
            fallback_seq,
            secret_values,
        )
    )


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
    context: OrgContext,
    secret_values: Collection[str] | None = None,
) -> list[StoredProtocolEvent]:
    conversation = await load_private_conversation(db, conversation_id, context)
    if conversation is None:
        raise conversation_not_found()
    if conversation.agent_lifecycle_state == "purged":
        from app.services.conversation_history_service import read_purged_history_content

        retained = await read_purged_history_content(db, conversation)
        events: list[StoredProtocolEvent] = []
        for retained_event in retained.events:
            record = MessageEvent(
                conversation_id=conversation.id,
                assistant_msg_id=retained_event.assistant_message_id,
                events=[],
            )
            for index, raw in enumerate(retained_event.events, start=1):
                if isinstance(raw, Mapping):
                    events.extend(
                        _stored_events_from_raw(
                            raw,
                            record=record,
                            fallback_seq=index,
                            secret_values=(),
                        )
                    )
        return resequence_protocol_events(events)
    return await load_live_protocol_events(
        LiveProtocolEventsRequest(db, conversation, context, secret_values)
    )


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
