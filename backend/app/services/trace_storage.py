"""Trace storage — persist SSE event sequences per assistant turn.

W5 Phase 1: end-of-turn batch persistence. Events accumulate in a list during
``stream_agent_response`` and are flushed once when the stream completes (or
fails). Sufficient for W6 shared-page rendering.

W3-out M2: partial flush — ``append_events`` UPSERT은 stream 진행 중 32 events
또는 2초마다 호출되어 ``status='streaming'`` row를 점진적으로 채운다.
``finalize_turn`` 은 message_end / 정상 종료 / 실패 분기에서 한 번 호출되어
``status`` 를 종결 상태로 갱신하고 ``linked_message_ids`` 를 부착한다.
기존 ``record_turn`` 은 [DEPRECATED] backward-compat shim — 신규 호출 경로는
``append_events`` + ``finalize_turn`` 조합을 사용한다 (W3-out M6 retrospective).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.message_utils import parse_msg_id
from app.models.message_event import MessageEvent

# Status enum for message_events.status — DB CHECK 제약과 일치.
TraceStatus = Literal["streaming", "completed", "failed"]

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


def _resolve_linked_ids(
    conversation_id: uuid.UUID, raw_msg_ids: list[str] | None
) -> list[str] | None:
    if not raw_msg_ids:
        return None
    return [str(parse_msg_id(raw, conversation_id, idx)) for idx, raw in enumerate(raw_msg_ids)]


async def append_events(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    assistant_msg_id: str,
    events_chunk: list[dict[str, Any]],
    status: TraceStatus = "streaming",
) -> MessageEvent | None:
    """Partial flush — UPSERT a chunk of events into the turn's row.

    W3-out M2의 핵심 hot path. ``stream_agent_response`` 의 emit 클로저가
    32 events 또는 2초마다 호출. dedup-by-id로 boundary 중복(같은 chunk가
    재시도 등으로 두 번 도달)을 방지한다.

    Behavior:
    - row가 없으면 INSERT. ``events`` 는 ``events_chunk`` 그대로.
    - row가 있으면 ``events = existing_events + (events_chunk - existing_ids)``
      로 application-side concat 후 UPDATE.
    - ``last_event_id`` = ``events_chunk[-1]["id"]`` (chunk 비면 no-op).
    - ``status`` 는 caller가 명시. 기본 'streaming'.
    - ``updated_at`` 은 model의 ``onupdate=now()`` 가 자동 갱신
      (INSERT 시도 server_default 적용).

    PoC 단순화 — application-side dedup은 row당 events 수가 작은 동안
    저렴하다. 한 turn이 5000 events를 넘기 시작하면 SQL CTE 또는 별도
    ``events_chunks`` 테이블로 옮긴다 (plan의 후속 PR 항목).

    Caller commits the session.

    Returns:
        Updated/inserted MessageEvent row, or ``None`` if ``events_chunk`` 가 비었음.
    """
    if not events_chunk:
        return None

    last_id = events_chunk[-1].get("id") if events_chunk else None
    last_event_id = last_id if isinstance(last_id, str) and last_id else None

    existing = await db.execute(
        select(MessageEvent).where(MessageEvent.assistant_msg_id == assistant_msg_id)
    )
    record = existing.scalar_one_or_none()

    if record is None:
        record = MessageEvent(
            conversation_id=conversation_id,
            assistant_msg_id=assistant_msg_id,
            events=list(events_chunk),
            last_event_id=last_event_id,
            status=status,
            # completed_at은 finalize_turn에서 set. streaming 동안은 None.
        )
        db.add(record)
        return record

    # Dedup-by-id: 기존 events에 이미 있는 id는 skip하고 새 id만 append.
    existing_ids: set[str] = {
        evt.get("id")  # type: ignore[misc]
        for evt in (record.events or [])
        if isinstance(evt.get("id"), str)
    }
    new_events = [
        evt
        for evt in events_chunk
        if not (isinstance(evt.get("id"), str) and evt.get("id") in existing_ids)
    ]

    if not new_events and last_event_id == record.last_event_id and record.status == status:
        # Nothing changed — skip UPDATE to avoid useless WAL write.
        return record

    merged_events: list[dict[str, Any]] = list(record.events or []) + new_events
    record.events = merged_events
    if last_event_id:
        record.last_event_id = last_event_id
    record.status = status
    # ``onupdate=`` 가 ORM flush 시 updated_at을 갱신.
    return record


async def finalize_turn(
    db: AsyncSession,
    *,
    assistant_msg_id: str,
    status: TraceStatus = "completed",
    raw_msg_ids: list[str] | None = None,
    conversation_id: uuid.UUID | None = None,
    external_trace_provider: str | None = None,
    external_trace_id: str | None = None,
    external_trace_url: str | None = None,
) -> MessageEvent | None:
    """Mark a streaming turn as finished and attach linked message ids.

    W3-out M2 — ``_persist_trace`` 의 final-write 책임을 흡수. 정상 종료
    (message_end), 예외, GraphInterrupt 모두 finally 블록에서 호출.

    Behavior:
    - row 없음 → ``None`` 반환 (events 0건이라 append_events가 한 번도 안
      불린 비정상 케이스. caller가 record_turn fallback 결정).
    - row 있음 → status, completed_at, updated_at 갱신. raw_msg_ids 제공 시
      linked_message_ids 갱신 (NULL 덮어쓰기 OK).

    ``conversation_id`` 는 raw_msg_ids → linked_ids 변환에만 필요. 이미 row
    가 있으면 row의 conversation_id를 사용해 caller가 생략 가능.

    Caller commits the session.
    """
    existing = await db.execute(
        select(MessageEvent).where(MessageEvent.assistant_msg_id == assistant_msg_id)
    )
    record = existing.scalar_one_or_none()
    if record is None:
        return None

    record.status = status
    record.completed_at = datetime.now(UTC).replace(tzinfo=None)
    if raw_msg_ids:
        conv_id = conversation_id or record.conversation_id
        record.linked_message_ids = _resolve_linked_ids(conv_id, raw_msg_ids)
    if external_trace_provider or external_trace_id or external_trace_url:
        record.external_trace_provider = external_trace_provider
        record.external_trace_id = external_trace_id
        record.external_trace_url = external_trace_url
    return record


async def record_turn(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    events: list[dict[str, Any]],
    raw_msg_ids: list[str] | None = None,
    status: TraceStatus = "completed",
    external_trace_provider: str | None = None,
    external_trace_id: str | None = None,
    external_trace_url: str | None = None,
) -> MessageEvent | None:
    """[DEPRECATED — 신규 호출자 추가 금지] one-shot turn persistence shim.

    W3-out 트랙 종료 (M6) 시점 기준 production 호출자는 단 1곳:
    ``routers/conversations._finalize_trace`` 의 fallback path (``finalize_turn``
    이 row 를 못 찾고 + ``trace_sink`` 에 events 가 모인 비정상 종료 케이스).
    나머지 호출자는 모두 테스트 (``test_trace_storage.py`` 12건 + ``test_shares_
    router.py`` 시드).

    **신규 코드는 반드시** ``append_events`` (partial flush) + ``finalize_turn``
    조합을 사용. ``record_turn`` 추가 호출은 invariant (``IntegrityError`` 보장
    vs ``append_events`` UPSERT) 가 분산되어 디버깅을 어렵게 한다.

    제거 절차 (별도 PR):
    1. ``finalize_turn`` 에 ``events_fallback: list | None = None`` 옵션 추가
       — 호출자가 row 부재 시 events 로 새 row insert + IntegrityError invariant
       유지하는 분기 흡수.
    2. production 호출자 1곳을 ``finalize_turn(events_fallback=trace_sink)`` 로
       마이그레이션.
    3. test_trace_storage.py 12건을 ``finalize_turn(events_fallback=...)`` 또는
       ``append_events`` 패턴으로 이전.
    4. ``record_turn`` 삭제.

    --- 기존 contract (현 호출자가 의존) ---
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
    last_id = last_event_id if isinstance(last_event_id, str) and last_event_id else None

    linked_ids = _resolve_linked_ids(conversation_id, raw_msg_ids)

    now = datetime.now(UTC).replace(tzinfo=None)
    record = MessageEvent(
        conversation_id=conversation_id,
        assistant_msg_id=msg_id,
        events=events,
        last_event_id=last_id,
        linked_message_ids=linked_ids,
        external_trace_provider=external_trace_provider,
        external_trace_id=external_trace_id,
        external_trace_url=external_trace_url,
        status=status,
        completed_at=now,
        # ORM-level explicit set — server_default(now())와 별도로 SQLite/PG
        # 일관성과 회귀 가드. (model의 default lambda + server_default가
        # 채우지만 이중 안전망.)
        updated_at=now,
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


async def get_trace_by_msg_id(db: AsyncSession, assistant_msg_id: str) -> MessageEvent | None:
    """Lookup a single turn trace by its assistant message id."""
    result = await db.execute(
        select(MessageEvent).where(MessageEvent.assistant_msg_id == assistant_msg_id)
    )
    return result.scalar_one_or_none()
