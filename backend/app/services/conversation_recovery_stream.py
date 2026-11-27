"""Read the committed protocol prefix inside the recovery owner's authorized transaction."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.recovery_stream import RecoveryOutputStarted, RecoveryStreamCursor
from app.models.message_event import MessageEvent
from app.services.conversation_recovery_steers import RecoverySteers
from app.services.trace_storage import load_events


async def load_recovery_stream_cursor(
    db: AsyncSession,
    *,
    conversation_id: UUID,
    run_id: UUID,
    steers: RecoverySteers | None = None,
    observe_prefix: Callable[[Sequence[dict[str, Any]]], None] | None = None,
) -> RecoveryStreamCursor:
    record = await db.scalar(
        select(MessageEvent).where(
            MessageEvent.conversation_id == conversation_id,
            MessageEvent.assistant_msg_id == str(run_id),
        )
    )
    if record is not None and record.status != "streaming":
        raise RecoveryOutputStarted("Chat stream already closed")
    events = await load_events(db, record) if record is not None else ()
    if record is not None and record.last_event_id != (events[-1].get("id") if events else None):
        raise RecoveryOutputStarted("Incomplete chat stream prefix")
    proof = steers or RecoverySteers()
    cursor = RecoveryStreamCursor.from_events(
        str(run_id),
        str(conversation_id),
        events,
        verified_steer_events=proof.public_ids(events, conversation_id, run_id),
        claimed_steers=proof.claimed,
    )
    if observe_prefix is not None:
        observe_prefix(events)
    return cursor
