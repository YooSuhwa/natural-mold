"""Frozen m77 message_attachments fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "message_attachments",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True, primary_key=False, index=True),
        sa.Column("message_id", sa.String(length=100), nullable=True, primary_key=False),
        sa.Column("filename", sa.String(length=255), nullable=False, primary_key=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False, primary_key=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False, primary_key=False),
        sa.Column("url", sa.String(length=500), nullable=False, primary_key=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name=None, ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
    )
