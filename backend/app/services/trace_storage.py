"""Trace storage — persist SSE event sequences per assistant turn.

W5 Phase 1 (this module): end-of-turn batch persistence. Events accumulate in
a list during ``stream_agent_response`` and are flushed once when the stream
completes (or fails). Sufficient for W6 shared-page rendering.

Future (W3-out): real-time append + event broker so reconnecting clients can
replay in-progress turns. The schema (``message_events`` table) is already
shaped for that — ``last_event_id`` lets a client request "everything after
event X". Phase 1 just doesn't do mid-turn writes yet.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message_event import MessageEvent

logger = logging.getLogger(__name__)


def _extract_msg_id(events: list[dict[str, Any]]) -> str | None:
    """``message_start`` 이벤트의 ``data.id``를 assistant message id로 사용.

    스트림 직후 graph 에러로 ``message_start``가 안 발행된 비정상 케이스에서는
    ``None``을 돌려 caller가 새 UUID를 할당하게 한다.
    """
    for evt in events:
        if evt.get("event") == "message_start":
            data = evt.get("data") or {}
            value = data.get("id")
            if isinstance(value, str) and value:
                return value
    return None


async def record_turn(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    events: list[dict[str, Any]],
    raw_msg_ids: list[str] | None = None,
) -> MessageEvent | None:
    """Persist all events emitted during one assistant turn.

    No-op when ``events`` is empty (interrupt before message_start, etc.).
    Caller commits the session.

    ``raw_msg_ids`` (W6 정확도): 이 turn 동안 노출된 langchain 메시지 raw id
    목록 (중복 제거됨, streaming 순서 보존). ``parse_msg_id``로 UUID 변환 후
    ``linked_message_ids`` 컬럼에 저장. None이면 컬럼은 NULL.
    """
    if not events:
        return None

    msg_id = _extract_msg_id(events) or str(uuid.uuid4())
    last_event_id = events[-1].get("id")
    last_id = (
        last_event_id if isinstance(last_event_id, str) and last_event_id else None
    )

    linked_ids: list[str] | None = None
    if raw_msg_ids:
        from app.agent_runtime.message_utils import parse_msg_id

        linked_ids = [
            str(parse_msg_id(raw, conversation_id, idx))
            for idx, raw in enumerate(raw_msg_ids)
        ]

    record = MessageEvent(
        conversation_id=conversation_id,
        assistant_msg_id=msg_id,
        events=events,
        last_event_id=last_id,
        linked_message_ids=linked_ids,
        completed_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(record)
    return record


async def get_traces_for_conversation(
    db: AsyncSession, conversation_id: uuid.UUID
) -> list[MessageEvent]:
    """Return all turn traces for a conversation, oldest-first."""
    result = await db.execute(
        select(MessageEvent)
        .where(MessageEvent.conversation_id == conversation_id)
        .order_by(MessageEvent.created_at)
    )
    return list(result.scalars().all())


async def get_trace_by_msg_id(
    db: AsyncSession, assistant_msg_id: str
) -> MessageEvent | None:
    """Lookup a single turn trace by its assistant message id."""
    result = await db.execute(
        select(MessageEvent).where(MessageEvent.assistant_msg_id == assistant_msg_id)
    )
    return result.scalar_one_or_none()
