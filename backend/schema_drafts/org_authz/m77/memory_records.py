"""Frozen m77 memory_records fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "memory_records",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False, index=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True, primary_key=False, index=True),
        sa.Column("scope", sa.String(length=20), nullable=False, primary_key=False),
        sa.Column("content", sa.Text(), nullable=False, primary_key=False),
        sa.Column("reason", sa.Text(), nullable=True, primary_key=False),
        sa.Column("store_path", sa.String(length=200), nullable=False, primary_key=False),
        sa.Column(
            "source_conversation_id", sa.Uuid(), nullable=True, primary_key=False, index=True
        ),
        sa.Column("source_message_id", sa.String(length=128), nullable=True, primary_key=False),
        sa.Column("source_run_id", sa.String(length=128), nullable=True, primary_key=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            primary_key=False,
            default="active",
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
            onupdate=defaults.now_naive,
        ),
        sa.Column("deleted_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.ForeignKeyConstraint(
            ["source_conversation_id"], ["conversations.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
    )
