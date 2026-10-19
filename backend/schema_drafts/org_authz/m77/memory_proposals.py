"""Frozen m77 memory_proposals fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "memory_proposals",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False, index=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True, primary_key=False, index=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True, primary_key=False, index=True),
        sa.Column("source_run_id", sa.String(length=128), nullable=True, primary_key=False),
        sa.Column("scope", sa.String(length=20), nullable=False, primary_key=False),
        sa.Column("content", sa.Text(), nullable=False, primary_key=False),
        sa.Column("reason", sa.Text(), nullable=True, primary_key=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            primary_key=False,
            default="pending",
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.Column("resolved_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
    )
