"""Persist user-selected conversation summary snapshots."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m76_pinned_conv_summaries"
down_revision = "m75_mcp_apps_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_pinned_summaries",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("source_message_id", sa.String(length=255), nullable=False),
        sa.Column("source_branch_checkpoint_id", sa.String(length=64), nullable=False),
        sa.Column("snapshot_text", sa.Text(), nullable=False),
        sa.Column("source_text_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(snapshot_text) <= 4000",
            name="ck_conversation_pinned_summaries_snapshot_bounded",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("conversation_id"),
    )


def downgrade() -> None:
    op.drop_table("conversation_pinned_summaries")
