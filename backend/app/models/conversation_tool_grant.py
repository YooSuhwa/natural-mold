"""Tools a person allowed for one conversation (approval-modes-plan 3.3, Q5).

A row lifts the approval of one tool, by name, in that conversation only, for later turns
and resumes too, within the company cap. Revoking (by the person, or because the agent's
tool approval settings changed) stamps ``revoked_at``; at most one live row per tool.
RLS child of ``conversations``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ConversationToolGrant(Base):
    __tablename__ = "conversation_tool_grants"
    __table_args__ = (
        Index(
            "uq_conversation_tool_grants_live",
            "conversation_id",
            "tool_name",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
            sqlite_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    granted_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(default=_now_naive, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)


__all__ = ["ConversationToolGrant"]
