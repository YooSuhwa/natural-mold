"""Frozen m77 message_event_chunks fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "message_event_chunks",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("message_event_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("assistant_msg_id", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("seq_start", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("seq_end", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("first_event_id", sa.String(length=255), nullable=True, primary_key=False),
        sa.Column("last_event_id", sa.String(length=255), nullable=True, primary_key=False),
        sa.Column("event_ids", sa.JSON(), nullable=False, primary_key=False, default=list),
        sa.Column("events", sa.JSON(), nullable=False, primary_key=False, default=list),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["message_event_id"], ["message_events.id"], name=None, ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
        sa.UniqueConstraint(
            "message_event_id", "first_event_id", name="uq_message_event_chunks_event_first_id"
        ),
    )
    sa.Index(
        "ix_message_event_chunks_assistant_seq",
        metadata.tables["message_event_chunks"].c["assistant_msg_id"],
        metadata.tables["message_event_chunks"].c["seq_start"],
        unique=False,
    )
    sa.Index(
        "ix_message_event_chunks_conversation_created",
        metadata.tables["message_event_chunks"].c["conversation_id"],
        metadata.tables["message_event_chunks"].c["created_at"],
        unique=False,
    )
    sa.Index(
        "ix_message_event_chunks_message_seq",
        metadata.tables["message_event_chunks"].c["message_event_id"],
        metadata.tables["message_event_chunks"].c["seq_start"],
        unique=False,
    )
