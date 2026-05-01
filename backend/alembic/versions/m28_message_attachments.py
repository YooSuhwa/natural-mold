"""M28: ``message_attachments`` — file uploads attached to chat messages.

Revision ID: m28_message_attachments
Revises: m27_message_feedback
Create Date: 2026-05-01

Bytes live on disk under ``settings.upload_dir``. The row carries the public
URL the frontend uses to render preview cards. ``message_id`` is the
LangGraph checkpoint message id; nullable until the user actually sends the
message that references this upload (orphan rows are reaped by a future job).

Reversible.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m28_message_attachments"
down_revision = "m27_message_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_attachments",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            sa.UUID(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("message_id", sa.String(length=100), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_message_attachments_message_id",
        "message_attachments",
        ["message_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_message_attachments_message_id", table_name="message_attachments"
    )
    op.drop_table("message_attachments")
