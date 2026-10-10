"""Frozen m77 tools fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "tools",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            primary_key=False,
            default=False,
            server_default="false",
        ),
        sa.Column("definition_key", sa.String(length=80), nullable=False, primary_key=False),
        sa.Column("name", sa.String(length=150), nullable=False, primary_key=False),
        sa.Column("description", sa.Text(), nullable=True, primary_key=False),
        sa.Column("parameters", sa.JSON(), nullable=True, primary_key=False, default=dict),
        sa.Column("credential_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, primary_key=False, default=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True, primary_key=False),
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
        sa.CheckConstraint(
            "(is_system = false) OR (user_id IS NULL)", name="ck_tools_system_user_null"
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["credentials.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
    )
