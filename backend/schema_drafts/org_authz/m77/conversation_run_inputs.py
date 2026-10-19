"""Frozen m77 conversation_run_inputs fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "conversation_run_inputs",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("conversation_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("run_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("client_request_id", sa.String(length=200), nullable=False, primary_key=False),
        sa.Column("source", sa.String(length=30), nullable=False, primary_key=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, primary_key=False, default="pending"
        ),
        sa.Column("priority", sa.Integer(), nullable=False, primary_key=False, default=0),
        sa.Column("position", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("revision", sa.Integer(), nullable=False, primary_key=False, default=1),
        sa.Column("input_payload", sa.JSON(), nullable=False, primary_key=False),
        sa.Column("attachment_ids", sa.JSON(), nullable=False, primary_key=False, default=list),
        sa.Column("checkpoint_id", sa.String(length=64), nullable=True, primary_key=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
            onupdate=defaults.now_naive,
        ),
        sa.CheckConstraint(
            (
                "(status IN ('pending', 'canceled') AND run_id IS NULL) OR (status IN "
                "('claimed', 'failed') AND run_id IS NOT NULL)"
            ),
            name="ck_conversation_run_inputs_binding",
        ),
        sa.CheckConstraint(
            "revision >= 1 AND position >= 1", name="ck_conversation_run_inputs_revision_position"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'canceled', 'failed')",
            name="ck_conversation_run_inputs_status",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["conversation_runs.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
        sa.UniqueConstraint(
            "conversation_id", "client_request_id", name="uq_conversation_run_inputs_request"
        ),
        sa.UniqueConstraint("run_id", name="uq_conversation_run_inputs_run"),
    )
    sa.Index(
        "ix_conversation_run_inputs_pending_order",
        metadata.tables["conversation_run_inputs"].c["conversation_id"],
        metadata.tables["conversation_run_inputs"].c["status"],
        metadata.tables["conversation_run_inputs"].c["priority"],
        metadata.tables["conversation_run_inputs"].c["position"],
        unique=False,
    )
