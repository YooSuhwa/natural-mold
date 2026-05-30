"""M51: message_events external trace correlation.

Revision ID: m51_msg_events_ext_trace
Revises: m50_schedule_run_metadata
Create Date: 2026-05-30
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m51_msg_events_ext_trace"
down_revision = "m50_schedule_run_metadata"
branch_labels = None
depends_on = None

_INDEX_NAME = "idx_message_events_external_trace"


def upgrade() -> None:
    op.add_column(
        "message_events",
        sa.Column("external_trace_provider", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "message_events",
        sa.Column("external_trace_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "message_events",
        sa.Column("external_trace_url", sa.String(length=500), nullable=True),
    )
    op.create_index(
        _INDEX_NAME,
        "message_events",
        ["external_trace_provider", "external_trace_id"],
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="message_events")
    op.drop_column("message_events", "external_trace_url")
    op.drop_column("message_events", "external_trace_id")
    op.drop_column("message_events", "external_trace_provider")
