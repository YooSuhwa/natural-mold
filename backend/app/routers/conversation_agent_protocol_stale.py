from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import StoredProtocolEvent
from app.config import settings
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.routers.conversation_agent_protocol_replay import (
    load_protocol_events,
    protocol_stale_event,
)
from app.services import conversation_run_service
from app.services.conversation_audit_service import record_conversation_run_audit


@dataclass(frozen=True, slots=True)
class StaleProtocolReplay:
    run: ConversationRun
    events: Sequence[StoredProtocolEvent]
    stale_event: StoredProtocolEvent


def _run_is_stale(run: ConversationRun) -> bool:
    reference = run.heartbeat_at or run.started_at or run.created_at
    stale_before = utc_now_naive() - timedelta(seconds=settings.chat_run_stale_after_seconds)
    return reference <= stale_before


async def maybe_mark_stale_active_run(
    db: AsyncSession,
    *,
    conversation: Conversation,
    run_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
) -> StaleProtocolReplay | None:
    run = await conversation_run_service.get_run_for_user(
        db,
        conversation_id=conversation.id,
        run_id=run_id,
        user_id=user.id,
        for_update=True,
    )
    if run is None or not run.is_active or not _run_is_stale(run):
        return None

    events = await load_protocol_events(db, conversation.id)
    stale_event = protocol_stale_event(
        run_id=str(run.id),
        thread_id=str(conversation.id),
        seq=max((event["seq"] for event in events), default=0) + 1,
        last_event_id=run.last_event_id,
    )
    await conversation_run_service.transition_run(
        db,
        run,
        "stale",
        error_code="worker_lost",
        error_message="Active run has no local worker and heartbeat is stale.",
    )
    await conversation_run_service.finalize_run_outputs_for_status(db, run, "stale")
    await record_conversation_run_audit(
        db,
        action="conversation.run_stale",
        run=run,
        user=user,
        request=request,
        status="stale",
    )
    await db.commit()
    return StaleProtocolReplay(run=run, events=events, stale_event=stale_event)
