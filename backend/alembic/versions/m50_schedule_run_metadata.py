"""M50: add schedule run metadata.

Revision ID: m50_schedule_run_metadata
Revises: m49_schedule_conversation_policy
Create Date: 2026-05-30
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m50_schedule_run_metadata"
down_revision = "m49_schedule_conversation_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_trigger_runs",
        sa.Column("source", sa.String(length=20), nullable=False, server_default="scheduled"),
    )
    op.add_column("agent_trigger_runs", sa.Column("output_preview", sa.Text(), nullable=True))
    op.add_column("agent_trigger_runs", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column("agent_trigger_runs", sa.Column("thread_id", sa.String(length=80), nullable=True))
    op.add_column(
        "agent_trigger_runs",
        sa.Column("checkpoint_id", sa.String(length=80), nullable=True),
    )
    op.add_column("agent_trigger_runs", sa.Column("trace_id", sa.String(length=80), nullable=True))
    op.alter_column("agent_trigger_runs", "source", server_default=None)


def downgrade() -> None:
    op.drop_column("agent_trigger_runs", "trace_id")
    op.drop_column("agent_trigger_runs", "checkpoint_id")
    op.drop_column("agent_trigger_runs", "thread_id")
    op.drop_column("agent_trigger_runs", "duration_ms")
    op.drop_column("agent_trigger_runs", "output_preview")
    op.drop_column("agent_trigger_runs", "source")
