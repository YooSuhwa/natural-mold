"""Frozen m77 message_events fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "message_events",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("conversation_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column(
            "assistant_msg_id", sa.String(length=64), nullable=False, primary_key=False, unique=True
        ),
        sa.Column("events", sa.JSON(), nullable=False, primary_key=False, default=list),
        sa.Column("last_event_id", sa.String(length=255), nullable=True, primary_key=False),
        sa.Column("linked_message_ids", sa.JSON(), nullable=True, primary_key=False),
        sa.Column(
            "external_trace_provider", sa.String(length=40), nullable=True, primary_key=False
        ),
        sa.Column("external_trace_id", sa.String(length=128), nullable=True, primary_key=False),
        sa.Column("external_trace_url", sa.String(length=500), nullable=True, primary_key=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            primary_key=False,
            default="completed",
            server_default="completed",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
            onupdate=defaults.now_naive,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name=None, ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
    )
