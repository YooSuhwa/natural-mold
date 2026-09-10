"""Retain one discoverable side chat per parent conversation."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m77_side_chat_link"
down_revision = "m76_pinned_conv_summaries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("side_chat_parent_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_conversations_side_chat_parent",
        "conversations",
        "conversations",
        ["side_chat_parent_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_conversations_side_chat_parent", "conversations", ["side_chat_parent_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_conversations_side_chat_parent", "conversations", type_="unique")
    op.drop_constraint("fk_conversations_side_chat_parent", "conversations", type_="foreignkey")
    op.drop_column("conversations", "side_chat_parent_id")
