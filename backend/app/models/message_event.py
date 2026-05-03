from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


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
    last_event_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    # 스트림 종료 시각. None이면 진행 중(Phase 1은 종료 시점에만 한 번 기록하므로
    # 항상 생성과 동시에 set되지만, W3-out 진행형 영속화 도입 시 의미가 갈라진다).
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
