from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import String, cast, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.message_utils import parse_msg_id
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from app.schemas.conversation_run_message_links import ConversationRunMessageLinkResponse


@dataclass(frozen=True, slots=True)
class RunMessageLinkQuery:
    """Typed owner-scoped request for durable message links."""

    conversation_id: uuid.UUID
    user_id: uuid.UUID
    message_ids: Sequence[str]


async def list_run_message_links(
    db: AsyncSession,
    query: RunMessageLinkQuery,
) -> list[ConversationRunMessageLinkResponse]:
    """Return bounded historical message links without loading trace bodies."""
    requested = tuple(dict.fromkeys(query.message_ids))
    if not requested:
        return []

    if db.get_bind().dialect.name == "sqlite":
        linked_values = func.json_each(MessageEvent.linked_message_ids).table_valued(
            "value",
            joins_implicitly=True,
        )
    else:
        linked_values = func.json_array_elements_text(MessageEvent.linked_message_ids).table_valued(
            "value", joins_implicitly=True
        )

    canonical_by_requested = {
        message_id: str(parse_msg_id(message_id, query.conversation_id, 0))
        for message_id in requested
    }
    canonical_ids = tuple(dict.fromkeys(canonical_by_requested.values()))
    linked_rows = (
        select(
            linked_values.c.value.label("message_id"),
            ConversationRun.id.label("run_id"),
        )
        .select_from(MessageEvent)
        .join(linked_values, true())
        .join(
            ConversationRun,
            func.replace(MessageEvent.assistant_msg_id, "-", "")
            == func.replace(cast(ConversationRun.id, String), "-", ""),
        )
        .where(
            MessageEvent.conversation_id == query.conversation_id,
            ConversationRun.conversation_id == query.conversation_id,
            ConversationRun.user_id == query.user_id,
            linked_values.c.value.in_(canonical_ids),
        )
        .distinct()
    )
    rows = (await db.execute(linked_rows)).all()
    candidates: dict[str, set[uuid.UUID]] = {}
    for message_id, run_id in rows:
        candidates.setdefault(message_id, set()).add(run_id)
    selected = {
        message_id: next(iter(run_ids))
        for message_id, run_ids in candidates.items()
        if len(run_ids) == 1
    }

    return [
        ConversationRunMessageLinkResponse(
            message_id=message_id,
            run_id=selected[canonical_by_requested[message_id]],
        )
        for message_id in requested
        if canonical_by_requested[message_id] in selected
    ]
