from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ConversationPinnedSummary(Base):
    """User-selected assistant message snapshot for one conversation."""

    __tablename__ = "conversation_pinned_summaries"
    __table_args__ = (
        CheckConstraint(
            "length(snapshot_text) <= 4000",
            name="ck_conversation_pinned_summaries_snapshot_bounded",
        ),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_branch_checkpoint_id: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
