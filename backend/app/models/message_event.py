from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base

# W3-out M2: SSE turn lifecycle status. CHECK 제약으로 Postgres ENUM 대신
# 문자열 + 제약 조합 (alembic-friendly + dialect-agnostic).
STREAMING_STATUS_VALUES = ("streaming", "completed", "failed")
STREAM_EVENT_ID_MAX_LENGTH = 255


class MessageEvent(Base):
    """SSE event trace for one assistant turn.

    Stores the full event sequence emitted during a single
    ``stream_agent_response`` call, keyed by the assistant message id.
    Foundation for W3-out (resume from ``last_event_id``) and W6 (shared
    page tool/skill chip rendering).

    One row per turn — edits and regenerates produce new rows ordered by
    ``created_at`` rather than overwriting.
    """

    __tablename__ = "message_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    # ``stream_agent_response``의 msg_id (UUID 문자열). SSE event id 접두어로도 사용.
    assistant_msg_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # 발행된 SSE 이벤트 시퀀스. 각 항목 shape:
    #   {"id": "<msg_id>-<seq>", "event": "<name>", "data": {...}}
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    # 마지막으로 발행된 SSE event id — W3-out resume 시 ``> last_event_id`` 필터의 기준.
    last_event_id: Mapped[str | None] = mapped_column(
        String(STREAM_EVENT_ID_MAX_LENGTH), nullable=True
    )
    # W6 정확도 — 이 turn에서 생성된 assistant 메시지의 parsed UUID 목록.
    # ``MessageResponse.id``와 동일 형식이라 frontend가 직접 매칭 가능.
    # NULL은 m33 이전 row 또는 streaming이 메시지 id를 노출 안 한 경우.
    linked_message_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # External trace correlation for authenticated debug tooling. The public
    # /traces schema intentionally does not expose these fields.
    external_trace_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    external_trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_trace_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    # 스트림 종료 시각. None이면 진행 중(Phase 1은 종료 시점에만 한 번 기록하므로
    # 항상 생성과 동시에 set되지만, W3-out 진행형 영속화 도입 시 의미가 갈라진다).
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # W3-out M2 — turn lifecycle:
    #   'streaming'  : 진행 중. partial flush로 events가 점진적 추가됨.
    #   'completed'  : 정상 종료 (message_end 도달).
    #   'failed'     : 예외/abort. last_event_id는 마지막으로 받은 이벤트.
    # 기존 row(m34 이전)는 server_default 'completed'로 채워져 회귀 0.
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="completed",
        default="completed",
    )
    # 진행 중 partial flush마다 갱신. completed_at과 달리 streaming 동안
    # 매번 NOW()로 bump (heartbeat 역할 — stale broker GC 판정에도 활용 가능).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
    )


class MessageEventChunk(Base):
    """Append-only payload chunks for a streaming assistant turn."""

    __tablename__ = "message_event_chunks"
    __table_args__ = (
        UniqueConstraint(
            "message_event_id",
            "first_event_id",
            name="uq_message_event_chunks_event_first_id",
        ),
        Index("ix_message_event_chunks_message_seq", "message_event_id", "seq_start"),
        Index("ix_message_event_chunks_assistant_seq", "assistant_msg_id", "seq_start"),
        Index("ix_message_event_chunks_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    message_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("message_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    assistant_msg_id: Mapped[str] = mapped_column(String(64), nullable=False)
    seq_start: Mapped[int] = mapped_column(Integer, nullable=False)
    seq_end: Mapped[int] = mapped_column(Integer, nullable=False)
    first_event_id: Mapped[str | None] = mapped_column(
        String(STREAM_EVENT_ID_MAX_LENGTH), nullable=True
    )
    last_event_id: Mapped[str | None] = mapped_column(
        String(STREAM_EVENT_ID_MAX_LENGTH), nullable=True
    )
    event_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
