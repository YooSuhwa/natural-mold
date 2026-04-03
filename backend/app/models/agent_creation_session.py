from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AgentCreationSession(Base):
    __tablename__ = "agent_creation_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    conversation_history: Mapped[list[dict]] = mapped_column(  # type: ignore[type-arg]
        JSON, nullable=False, default=list
    )
    draft_config: Mapped[dict | None] = mapped_column(JSON)  # type: ignore[type-arg]
    status: Mapped[str] = mapped_column(String(20), default="in_progress")
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="creation_sessions")  # type: ignore[name-defined]
