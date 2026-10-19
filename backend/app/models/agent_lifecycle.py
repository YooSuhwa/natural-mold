from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.conversation_run import utc_now_naive

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class ConversationRunAgent(Base):
    """Immutable participant identity for one accepted conversation run."""

    __tablename__ = "conversation_run_agents"
    __table_args__ = (
        UniqueConstraint("run_id", "agent_id_snapshot", name="uq_conversation_run_agents_snapshot"),
        CheckConstraint("role IN ('root', 'subagent')", name="ck_conversation_run_agents_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation_runs.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    agent_id_snapshot: Mapped[uuid.UUID] = mapped_column(nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    path: Mapped[list[JsonValue]] = mapped_column(JSON, nullable=False)
    # F-14 (11.10, Q22): this participant's identity in the run (``tenancy.attribution``).
    invoker_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    resource_owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    on_behalf_of_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    credential_subject_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utc_now_naive, nullable=False
    )


__all__ = ["ConversationRunAgent"]
