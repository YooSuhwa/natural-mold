"""Frozen m77 user_memory_settings fixture definition; do not derive from live ORM."""

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "user_memory_settings",
        metadata,
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column(
            "memory_enabled",
            sa.Boolean(),
            nullable=False,
            primary_key=False,
            default=True,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "memory_read_enabled",
            sa.Boolean(),
            nullable=False,
            primary_key=False,
            default=True,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "memory_write_policy",
            sa.String(length=20),
            nullable=False,
            primary_key=False,
            default="ask",
            server_default="ask",
        ),
        sa.Column(
            "allowed_scopes",
            sa.String(length=20),
            nullable=False,
            primary_key=False,
            default="both",
            server_default="both",
        ),
        sa.Column(
            "trigger_memory_write_policy",
            sa.String(length=20),
            nullable=False,
            primary_key=False,
            default="off",
            server_default="off",
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", name=None),
    )
