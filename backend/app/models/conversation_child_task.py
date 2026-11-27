"""One linked, parent-bound child invocation and its ordered instructions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.conversation_run import utc_now_naive


class ConversationChildTask(Base):
    __tablename__ = "conversation_child_tasks"
    __table_args__ = (
        UniqueConstraint("run_id", "tool_call_id", name="uq_child_tasks_run_call"),
        Index("ix_child_tasks_current_run", "current_run_id"),
        CheckConstraint(
            "status IN ('running','awaiting_approval','completed','failed','cancelled')",
            name="ck_child_tasks_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_runs.id", ondelete="CASCADE"))
    current_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversation_runs.id", ondelete="CASCADE")
    )
    tool_call_id: Mapped[str] = mapped_column(String(256))
    child_agent_id: Mapped[UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(160))
    checkpoint_ns: Mapped[str] = mapped_column(String(1024))
    checkpoint_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="running")
    result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now_naive, onupdate=utc_now_naive)
    instructions: Mapped[list[ConversationChildInstruction]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ConversationChildInstruction.sequence",
        lazy="raise",
    )

    @property
    def checkpointed(self) -> bool:
        return self.checkpoint_id is not None


class ConversationChildInstruction(Base):
    __tablename__ = "conversation_child_instructions"
    __table_args__ = (
        UniqueConstraint("child_id", "client_request_id", name="uq_child_instructions_request"),
        UniqueConstraint("child_id", "sequence", name="uq_child_instructions_sequence"),
        CheckConstraint("sequence > 0", name="ck_child_instructions_sequence"),
        CheckConstraint(
            "status IN ('pending','delivering','applied','cancelled','review_required')",
            name="ck_child_instructions_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    child_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversation_child_tasks.id", ondelete="CASCADE")
    )
    client_request_id: Mapped[str] = mapped_column(String(200))
    sequence: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive)
    delivery_started_at: Mapped[datetime | None]
    applied_at: Mapped[datetime | None]
