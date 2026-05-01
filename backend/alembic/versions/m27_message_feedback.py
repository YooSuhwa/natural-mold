"""M27: ``message_feedbacks`` — thumbs up/down per (user, message).

Revision ID: m27_message_feedback
Revises: m26_mcp_health_and_system
Create Date: 2026-05-01

LangGraph owns the message stream, so ``message_id`` is a string identifier
(no FK). Uniqueness is enforced on ``(user_id, message_id)`` so a user can
only have one rating per message; toggling = upsert/delete.

Reversible.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m27_message_feedback"
down_revision = "m26_mcp_health_and_system"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_feedbacks",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("message_id", sa.String(length=100), nullable=False),
        sa.Column(
            "conversation_id",
            sa.UUID(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rating", sa.String(length=8), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "user_id", "message_id", name="uq_message_feedback_user_message"
        ),
    )
    op.create_index(
        "ix_message_feedbacks_message_id",
        "message_feedbacks",
        ["message_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_message_feedbacks_message_id", table_name="message_feedbacks")
    op.drop_table("message_feedbacks")
