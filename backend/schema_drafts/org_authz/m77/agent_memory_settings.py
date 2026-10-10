"""Frozen m77 agent_memory_settings fixture definition; do not derive from live ORM."""

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "agent_memory_settings",
        metadata,
        sa.Column("agent_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column(
            "memory_policy_override",
            sa.String(length=20),
            nullable=False,
            primary_key=False,
            default="inherit",
            server_default="inherit",
        ),
        sa.Column(
            "memory_scopes_override",
            sa.String(length=20),
            nullable=False,
            primary_key=False,
            default="inherit",
            server_default="inherit",
        ),
        sa.Column(
            "trigger_memory_policy_override",
            sa.String(length=20),
            nullable=False,
            primary_key=False,
            default="inherit",
            server_default="inherit",
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
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_id", name=None),
    )
