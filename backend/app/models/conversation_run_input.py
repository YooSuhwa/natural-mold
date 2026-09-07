from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.conversation_run import utc_now_naive

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class ConversationRunInput(Base):
    """Durable user input waiting to be bound to one conversation run."""

    __tablename__ = "conversation_run_inputs"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "client_request_id",
            name="uq_conversation_run_inputs_request",
        ),
        UniqueConstraint("run_id", name="uq_conversation_run_inputs_run"),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'canceled', 'failed')",
            name="ck_conversation_run_inputs_status",
        ),
        CheckConstraint(
            "revision >= 1 AND position >= 1",
            name="ck_conversation_run_inputs_revision_position",
        ),
        CheckConstraint(
            "(status IN ('pending', 'canceled') AND run_id IS NULL) OR "
            "(status IN ('claimed', 'failed') AND run_id IS NOT NULL)",
            name="ck_conversation_run_inputs_binding",
        ),
        Index(
            "ix_conversation_run_inputs_pending_order",
            "conversation_id",
            "status",
            "priority",
            "position",
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
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversation_runs.id", ondelete="SET NULL"), nullable=True
    )
    client_request_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    input_payload: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)
    attachment_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    checkpoint_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utc_now_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default=utc_now_naive,
        onupdate=utc_now_naive,
        nullable=False,
    )
