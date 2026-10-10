"""Frozen m77 conversation_runs fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "conversation_runs",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("conversation_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("parent_run_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("source", sa.String(length=30), nullable=False, primary_key=False),
        sa.Column("status", sa.String(length=20), nullable=False, primary_key=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, primary_key=False, default=False),
        sa.Column("worker_instance_id", sa.String(length=80), nullable=True, primary_key=False),
        sa.Column("interrupt_id", sa.String(length=200), nullable=True, primary_key=False),
        sa.Column("input_preview", sa.String(length=500), nullable=True, primary_key=False),
        sa.Column("last_event_id", sa.String(length=255), nullable=True, primary_key=False),
        sa.Column("error_code", sa.String(length=80), nullable=True, primary_key=False),
        sa.Column("error_message", sa.String(length=1000), nullable=True, primary_key=False),
        sa.Column("cancel_requested_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("cancel_reason", sa.String(length=20), nullable=True, primary_key=False),
        sa.Column("cancellation_acknowledged_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("started_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True, primary_key=False),
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
        sa.Column("metadata_json", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("runtime_policy_version", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("runtime_policy_hash", sa.String(length=64), nullable=True, primary_key=False),
        sa.Column("runtime_policy_source", sa.String(length=20), nullable=True, primary_key=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_run_id"], ["conversation_runs.id"], name=None, ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
    )
    sa.Index(
        "ix_conversation_runs_agent_created",
        metadata.tables["conversation_runs"].c["agent_id"],
        metadata.tables["conversation_runs"].c["created_at"],
        unique=False,
    )
    sa.Index(
        "ix_conversation_runs_conversation_created",
        metadata.tables["conversation_runs"].c["conversation_id"],
        metadata.tables["conversation_runs"].c["created_at"],
        unique=False,
    )
    sa.Index(
        "ix_conversation_runs_status_heartbeat",
        metadata.tables["conversation_runs"].c["status"],
        metadata.tables["conversation_runs"].c["heartbeat_at"],
        unique=False,
    )
    sa.Index(
        "ix_conversation_runs_user_status",
        metadata.tables["conversation_runs"].c["user_id"],
        metadata.tables["conversation_runs"].c["status"],
        unique=False,
    )
    sa.Index(
        "uq_conversation_runs_active_conversation",
        metadata.tables["conversation_runs"].c["conversation_id"],
        unique=True,
        sqlite_where=sa.text("is_active = 1"),
        postgresql_where=sa.text("is_active = true"),
    )
