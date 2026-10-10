"""Frozen m77 mcp_servers fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "mcp_servers",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False, index=True),
        sa.Column("name", sa.String(length=150), nullable=False, primary_key=False),
        sa.Column("description", sa.Text(), nullable=True, primary_key=False),
        sa.Column("transport", sa.String(length=20), nullable=False, primary_key=False),
        sa.Column("url", sa.String(length=500), nullable=True, primary_key=False),
        sa.Column("command", sa.String(length=500), nullable=True, primary_key=False),
        sa.Column("args", sa.JSON(), nullable=True, primary_key=False, default=list),
        sa.Column("env_vars", sa.JSON(), nullable=True, primary_key=False, default=dict),
        sa.Column("headers", sa.JSON(), nullable=True, primary_key=False, default=dict),
        sa.Column("credential_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, primary_key=False, default="unknown"
        ),
        sa.Column("last_pinged_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("last_tool_count", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("last_error", sa.Text(), nullable=True, primary_key=False),
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            primary_key=False,
            default=False,
            server_default="false",
        ),
        sa.Column("health_status", sa.String(length=20), nullable=True, primary_key=False),
        sa.Column("health_polled_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("health_message", sa.Text(), nullable=True, primary_key=False),
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
        sa.ForeignKeyConstraint(
            ["credential_id"], ["credentials.id"], name=None, ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
    )
