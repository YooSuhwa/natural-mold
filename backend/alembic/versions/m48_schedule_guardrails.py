"""M48: add schedule guardrails.

Revision ID: m48_schedule_guardrails
Revises: m47_schedule_productization
Create Date: 2026-05-30
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m48_schedule_guardrails"
down_revision = "m47_schedule_productization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_triggers",
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("agent_triggers", sa.Column("max_runs", sa.Integer(), nullable=True))
    op.add_column("agent_triggers", sa.Column("end_at", sa.DateTime(), nullable=True))
    op.add_column(
        "agent_triggers",
        sa.Column("auto_pause_after_failures", sa.Integer(), nullable=True),
    )
    op.alter_column("agent_triggers", "failure_count", server_default=None)


def downgrade() -> None:
    op.drop_column("agent_triggers", "auto_pause_after_failures")
    op.drop_column("agent_triggers", "end_at")
    op.drop_column("agent_triggers", "max_runs")
    op.drop_column("agent_triggers", "failure_count")
