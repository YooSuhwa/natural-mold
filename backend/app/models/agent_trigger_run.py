from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AgentTriggerRun(Base):
    __tablename__ = "agent_trigger_runs"
    __table_args__ = (Index("ix_agent_trigger_runs_tenant_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trigger_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_triggers.id", ondelete="SET NULL"), nullable=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    trigger_name_snapshot: Mapped[str] = mapped_column(String(120), nullable=False)
    # I-07: the trigger's company, so export/purge reach runs whose trigger is gone.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_agent_trigger_runs_tenant_id"),
        nullable=True,
    )
    conversation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversation_runs.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
    input_message: Mapped[str] = mapped_column(Text, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)  # F-08 (11.6)
    output_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    checkpoint_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    identity_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    agent_runtime_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    credential_subject_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None), nullable=False
    )

    trigger = relationship("AgentTrigger")
    conversation = relationship("Conversation")
