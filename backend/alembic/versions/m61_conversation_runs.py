"""M61: conversation runs.

Revision ID: m61_conversation_runs
Revises: m60_credential_oauth_states
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m61_conversation_runs"
down_revision = "m60_credential_oauth_states"
branch_labels = None
depends_on = None

RUN_STATUS_VALUES = (
    "queued",
    "running",
    "interrupted",
    "canceling",
    "canceled",
    "completed",
    "failed",
    "stale",
)
RUN_SOURCE_VALUES = ("chat", "start", "edit", "regenerate", "resume")


def upgrade() -> None:
    op.create_table(
        "conversation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("parent_run_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("worker_instance_id", sa.String(length=80), nullable=True),
        sa.Column("interrupt_id", sa.String(length=200), nullable=True),
        sa.Column("input_preview", sa.String(length=500), nullable=True),
        sa.Column("last_event_id", sa.String(length=80), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'interrupted', 'canceling', "
            "'canceled', 'completed', 'failed', 'stale')",
            name="ck_conversation_runs_status",
        ),
        sa.CheckConstraint(
            "source IN ('chat', 'start', 'edit', 'regenerate', 'resume')",
            name="ck_conversation_runs_source",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_run_id"],
            ["conversation_runs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversation_runs_conversation_created",
        "conversation_runs",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "ix_conversation_runs_agent_created",
        "conversation_runs",
        ["agent_id", "created_at"],
    )
    op.create_index(
        "ix_conversation_runs_user_status",
        "conversation_runs",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_conversation_runs_status_heartbeat",
        "conversation_runs",
        ["status", "heartbeat_at"],
    )
    op.create_index(
        "uq_conversation_runs_active_conversation",
        "conversation_runs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
        sqlite_where=sa.text("is_active = 1"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_conversation_runs_active_conversation",
        table_name="conversation_runs",
        postgresql_where=sa.text("is_active = true"),
        sqlite_where=sa.text("is_active = 1"),
    )
    op.drop_index("ix_conversation_runs_status_heartbeat", table_name="conversation_runs")
    op.drop_index("ix_conversation_runs_user_status", table_name="conversation_runs")
    op.drop_index("ix_conversation_runs_agent_created", table_name="conversation_runs")
    op.drop_index("ix_conversation_runs_conversation_created", table_name="conversation_runs")
    op.drop_table("conversation_runs")
