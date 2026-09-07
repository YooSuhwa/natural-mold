"""Add durable conversation input queue and cancellation acknowledgements."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m73_conversation_run_inputs"
down_revision = "m72_runtime_policy_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("queue_paused", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("conversations", sa.Column("queue_paused_at", sa.DateTime(), nullable=True))
    op.add_column("conversation_runs", sa.Column("cancel_reason", sa.String(20), nullable=True))
    op.add_column(
        "conversation_runs",
        sa.Column("cancellation_acknowledged_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "conversation_run_inputs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("client_request_id", sa.String(200), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("attachment_ids", sa.JSON(), nullable=False),
        sa.Column("checkpoint_id", sa.String(64), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["conversation_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'canceled', 'failed')",
            name="ck_conversation_run_inputs_status",
        ),
        sa.CheckConstraint(
            "revision >= 1 AND position >= 1",
            name="ck_conversation_run_inputs_revision_position",
        ),
        sa.CheckConstraint(
            "(status IN ('pending', 'canceled') AND run_id IS NULL) OR "
            "(status IN ('claimed', 'failed') AND run_id IS NOT NULL)",
            name="ck_conversation_run_inputs_binding",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "client_request_id",
            name="uq_conversation_run_inputs_request",
        ),
        sa.UniqueConstraint("run_id", name="uq_conversation_run_inputs_run"),
    )
    op.create_index(
        "ix_conversation_run_inputs_pending_order",
        "conversation_run_inputs",
        ["conversation_id", "status", "priority", "position"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_run_inputs_pending_order",
        table_name="conversation_run_inputs",
    )
    op.drop_table("conversation_run_inputs")
    with op.batch_alter_table("conversation_runs") as batch_op:
        batch_op.drop_column("cancellation_acknowledged_at")
        batch_op.drop_column("cancel_reason")
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_column("queue_paused_at")
        batch_op.drop_column("queue_paused")
