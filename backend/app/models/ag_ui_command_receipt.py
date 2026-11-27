"""Map an AG-UI client command to one durable execution or queued input."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.conversation_run import utc_now_naive
from app.models.resource_scope import RequiredOrgScope, resource_scope_constraints


class AgUiCommandReceipt(RequiredOrgScope, Base):
    __tablename__ = "ag_ui_command_receipts"
    __table_args__ = (
        *resource_scope_constraints("ag_ui_command_receipts", system=False),
        UniqueConstraint("run_id", name="uq_ag_ui_commands_run"),
        UniqueConstraint("input_id", name="uq_ag_ui_commands_input"),
        CheckConstraint(
            "(run_id IS NULL) <> (input_id IS NULL)", name="ck_ag_ui_commands_one_target"
        ),
        CheckConstraint(
            "delivery_encrypted IS NULL OR run_id IS NOT NULL", name="ck_ag_ui_commands_delivery"
        ),
        CheckConstraint(
            "length(public_run_id) BETWEEN 1 AND 200", name="ck_ag_ui_commands_public_id"
        ),
        CheckConstraint(
            "length(parent_public_run_id) BETWEEN 1 AND 200", name="ck_ag_ui_commands_parent_id"
        ),
        CheckConstraint("length(request_digest) = 64", name="ck_ag_ui_commands_digest"),
        CheckConstraint(
            "length(projection_version) BETWEEN 1 AND 16", name="ck_ag_ui_commands_projection"
        ),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    public_run_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    request_digest: Mapped[str] = mapped_column(String(64))
    projection_version: Mapped[str] = mapped_column(String(16), default="1", server_default="1")
    checkpoint_id: Mapped[str | None] = mapped_column(String(64))
    parent_public_run_id: Mapped[str | None] = mapped_column(String(200))
    # Defer until commit so deleting an entire conversation can cascade in either order.
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "conversation_runs.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"
        )
    )
    input_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "conversation_run_inputs.id",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        )
    )
    delivery_encrypted: Mapped[str | None] = mapped_column(Text, deferred_raiseload=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive)
