"""M30: ``share_links`` — public read-only access tokens for conversations.

Revision ID: m30_share_links
Revises: m29_conversation_active_branch
Create Date: 2026-05-01

A user can publish a conversation by issuing a ``share_token``; visitors then
fetch the conversation + messages without authentication. Revocation is a soft
delete (``revoked_at``) so the URL invalidates immediately and the row remains
for audit. Conversation deletion cascades.

Reversible.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m30_share_links"
down_revision = "m29_conversation_active_branch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "share_links",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column("share_token", sa.String(length=48), nullable=False),
        sa.Column(
            "conversation_id",
            sa.UUID(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("share_token", name="uq_share_links_token"),
    )
    op.create_index(
        "ix_share_links_share_token",
        "share_links",
        ["share_token"],
    )
    op.create_index(
        "ix_share_links_conversation_id",
        "share_links",
        ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_share_links_conversation_id", table_name="share_links")
    op.drop_index("ix_share_links_share_token", table_name="share_links")
    op.drop_table("share_links")
