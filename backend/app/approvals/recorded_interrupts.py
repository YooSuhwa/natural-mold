"""Retrieve the server-redacted approval display belonging to one paused run."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from app.services.trace_storage import load_events


async def recorded_approval_displays(
    db: AsyncSession, run: ConversationRun
) -> dict[str, Mapping[str, Any]]:
    record = await db.scalar(
        select(MessageEvent)
        .join(Conversation, Conversation.id == MessageEvent.conversation_id)
        .where(
            MessageEvent.assistant_msg_id == str(run.id),
            Conversation.id == run.conversation_id,
            Conversation.user_id == run.user_id,
            Conversation.org_id == run.org_id,
            Conversation.tenant_id == run.tenant_id,
        )
    )
    if record is None:
        return {}
    displays: dict[str, Mapping[str, Any]] = {}
    for event in await load_events(db, record):
        # Only the persisted, redacted canonical wire event is an inbox display.
        if (event.get("method"), event.get("thread_id"), event.get("run_id")) != (
            "input.requested",
            str(run.conversation_id),
            str(run.id),
        ):
            continue
        data = event.get("data")
        if isinstance(data, Mapping):
            ident, payload = data.get("interrupt_id"), data.get("payload")
            if isinstance(ident, str) and isinstance(payload, Mapping):
                displays[ident] = payload
    return displays
