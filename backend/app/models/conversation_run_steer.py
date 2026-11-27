"""Ordered, idempotent text directives addressed to an existing chat run."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.conversation_run import utc_now_naive
from app.models.resource_scope import RequiredOrgScope, resource_scope_constraints


class ConversationRunSteer(RequiredOrgScope, Base):
    __tablename__ = "conversation_run_steers"
    __table_args__ = (
        *resource_scope_constraints("conversation_run_steers", system=False),
        UniqueConstraint("conversation_id", "client_request_id", name="uq_run_steers_request"),
        UniqueConstraint("conversation_id", "sequence", name="uq_run_steers_sequence"),
        UniqueConstraint("queued_input_id", name="uq_run_steers_queued_input"),
        CheckConstraint("sequence >= 1", name="ck_run_steers_sequence"),
        CheckConstraint("length(text) BETWEEN 1 AND 12000", name="ck_run_steers_text_length"),
        CheckConstraint(
            "status IN ('pending', 'applied', 'queued', 'needs_review')",
            name="ck_run_steers_status",
        ),
        CheckConstraint(
            "(status = 'queued') = (queued_input_id IS NOT NULL)", name="ck_run_steers_queue"
        ),
        CheckConstraint(
            "(status = 'applied') = (applied_at IS NOT NULL)", name="ck_run_steers_applied"
        ),
        Index("ix_run_steers_delivery", "run_id", "status", "sequence"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    run_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_runs.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    client_request_id: Mapped[UUID]
    sequence: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive)
    # A claim commits before the middleware supplies any text to the graph. An unfinished
    # claim requires checkpoint reconciliation before a successor can receive the text.
    source_branch_checkpoint_id: Mapped[str | None] = mapped_column(String(64))
    delivery_started_at: Mapped[datetime | None]
    applied_at: Mapped[datetime | None]
    queued_input_id: Mapped[UUID | None] = mapped_column(ForeignKey("conversation_run_inputs.id"))
