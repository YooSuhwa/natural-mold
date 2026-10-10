"""Frozen m77 conversations fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "conversations",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("agent_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("title", sa.String(length=200), nullable=True, primary_key=False),
        sa.Column("source", sa.String(length=20), nullable=False, primary_key=False, default="ui"),
        sa.Column("side_chat_parent_id", sa.Uuid(), nullable=True, primary_key=False, unique=True),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, primary_key=False, default=False),
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
        sa.Column("message_timestamps", sa.JSON(), nullable=False, primary_key=False, default=dict),
        sa.Column(
            "active_branch_checkpoint_id", sa.String(length=64), nullable=True, primary_key=False
        ),
        sa.Column("unread_count", sa.Integer(), nullable=False, primary_key=False, default=0),
        sa.Column("last_read_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("last_unread_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column(
            "last_activity_source",
            sa.String(length=20),
            nullable=False,
            primary_key=False,
            default="user",
        ),
        sa.Column("runtime_policy_snapshot", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("runtime_policy_version", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("runtime_policy_hash", sa.String(length=64), nullable=True, primary_key=False),
        sa.Column("runtime_policy_source", sa.String(length=20), nullable=True, primary_key=False),
        sa.Column(
            "queue_paused",
            sa.Boolean(),
            nullable=False,
            primary_key=False,
            default=False,
            server_default="false",
        ),
        sa.Column("queue_paused_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.ForeignKeyConstraint(
            ["side_chat_parent_id"], ["conversations.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=None),
        sa.PrimaryKeyConstraint("id", name=None),
    )
