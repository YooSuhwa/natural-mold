"""Message feedback ORM — thumbs up/down per (user, message).

Messages live in the LangGraph checkpointer (no durable ``messages`` table),
so ``message_id`` is a free-form string identifier — typically the LangGraph
message UUID surfaced through ``MessageResponse.id``. Uniqueness is enforced
on ``(user_id, message_id)`` so each user keeps a single rating per message.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MessageFeedback(Base):
    __tablename__ = "message_feedbacks"
    __table_args__ = (
        UniqueConstraint("user_id", "message_id", name="uq_message_feedback_user_message"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # LangGraph checkpoint message id — no FK because messages aren't a DB table.
    message_id: Mapped[str] = mapped_column(String(100), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    rating: Mapped[str] = mapped_column(String(8), nullable=False)  # 'up' | 'down'
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
