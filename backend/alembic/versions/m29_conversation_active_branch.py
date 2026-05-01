"""M29: ``conversations.active_branch_checkpoint_id`` — track user-selected branch.

Revision ID: m29_conversation_active_branch
Revises: m28_message_attachments
Create Date: 2026-05-01

LangGraph checkpoints can fork into siblings when a user edits / regenerates.
This column stores the checkpoint id the user is currently viewing so the
next fork happens from the selected branch (not just "the latest one in
chronological order").

Reversible.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m29_conversation_active_branch"
down_revision = "m28_message_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("active_branch_checkpoint_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "active_branch_checkpoint_id")
