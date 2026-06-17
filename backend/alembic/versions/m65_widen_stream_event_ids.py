from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m65_widen_stream_event_ids"
down_revision = "m64_skill_builder_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "message_events",
        "last_event_id",
        existing_type=sa.String(length=80),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
    op.alter_column(
        "message_event_chunks",
        "first_event_id",
        existing_type=sa.String(length=80),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
    op.alter_column(
        "message_event_chunks",
        "last_event_id",
        existing_type=sa.String(length=80),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
    op.alter_column(
        "conversation_runs",
        "last_event_id",
        existing_type=sa.String(length=80),
        type_=sa.String(length=255),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "conversation_runs",
        "last_event_id",
        existing_type=sa.String(length=255),
        type_=sa.String(length=80),
        existing_nullable=True,
    )
    op.alter_column(
        "message_event_chunks",
        "last_event_id",
        existing_type=sa.String(length=255),
        type_=sa.String(length=80),
        existing_nullable=True,
    )
    op.alter_column(
        "message_event_chunks",
        "first_event_id",
        existing_type=sa.String(length=255),
        type_=sa.String(length=80),
        existing_nullable=True,
    )
    op.alter_column(
        "message_events",
        "last_event_id",
        existing_type=sa.String(length=255),
        type_=sa.String(length=80),
        existing_nullable=True,
    )
