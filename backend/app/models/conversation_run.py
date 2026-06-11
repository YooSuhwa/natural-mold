from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

RUN_ACTIVE_STATUSES = ("queued", "running", "canceling")
RUN_TERMINAL_STATUSES = ("completed", "failed", "interrupted", "canceled", "stale")
RUN_STATUS_VALUES = RUN_ACTIVE_STATUSES + RUN_TERMINAL_STATUSES


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ConversationRun(Base):
    __tablename__ = "conversation_runs"
    __table_args__ = (
        Index(
            "ix_conversation_runs_conversation_created",
            "conversation_id",
            "created_at",
        ),
        Index("ix_conversation_runs_agent_created", "agent_id", "created_at"),
        Index("ix_conversation_runs_user_status", "user_id", "status"),
        Index("ix_conversation_runs_status_heartbeat", "status", "heartbeat_at"),
        Index(
            "uq_conversation_runs_active_conversation",
            "conversation_id",
            unique=True,
            sqlite_where=text("is_active = 1"),
            postgresql_where=text("is_active = true"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversation_runs.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    worker_instance_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    interrupt_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input_preview: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_event_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utc_now_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default=utc_now_naive,
        onupdate=utc_now_naive,
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
