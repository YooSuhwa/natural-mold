"""M53: performance hot path indexes and trace chunks.

Revision ID: m53_performance_hot_path_indexes
Revises: m52_marketplace_item_icon_id
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m53_performance_hot_path_indexes"
down_revision = "m52_marketplace_item_icon_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_event_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_event_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("assistant_msg_id", sa.String(length=64), nullable=False),
        sa.Column("seq_start", sa.Integer(), nullable=False),
        sa.Column("seq_end", sa.Integer(), nullable=False),
        sa.Column("first_event_id", sa.String(length=80), nullable=True),
        sa.Column("last_event_id", sa.String(length=80), nullable=True),
        sa.Column("event_ids", sa.JSON(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_event_id"],
            ["message_events.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_event_id",
            "first_event_id",
            name="uq_message_event_chunks_event_first_id",
        ),
    )
    op.create_index(
        "ix_message_event_chunks_assistant_seq",
        "message_event_chunks",
        ["assistant_msg_id", "seq_start"],
    )
    op.create_index(
        "ix_message_event_chunks_conversation_created",
        "message_event_chunks",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "ix_message_event_chunks_message_seq",
        "message_event_chunks",
        ["message_event_id", "seq_start"],
    )

    op.create_index(
        "ix_conversations_agent_pinned_updated_id",
        "conversations",
        ["agent_id", "is_pinned", "updated_at", "id"],
    )
    op.create_index(
        "ix_conversations_agent_updated",
        "conversations",
        ["agent_id", "updated_at"],
    )
    op.create_index("ix_agents_user_updated", "agents", ["user_id", "updated_at"])
    op.create_index(
        "ix_agent_triggers_status_next_run",
        "agent_triggers",
        ["status", "next_run_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_triggers_status_next_run", table_name="agent_triggers")
    op.drop_index("ix_agents_user_updated", table_name="agents")
    op.drop_index("ix_conversations_agent_updated", table_name="conversations")
    op.drop_index("ix_conversations_agent_pinned_updated_id", table_name="conversations")
    op.drop_index("ix_message_event_chunks_message_seq", table_name="message_event_chunks")
    op.drop_index(
        "ix_message_event_chunks_conversation_created",
        table_name="message_event_chunks",
    )
    op.drop_index("ix_message_event_chunks_assistant_seq", table_name="message_event_chunks")
    op.drop_table("message_event_chunks")
