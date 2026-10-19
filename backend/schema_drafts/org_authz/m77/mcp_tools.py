"""Frozen m77 mcp_tools fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "mcp_tools",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("server_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("name", sa.String(length=150), nullable=False, primary_key=False),
        sa.Column("description", sa.Text(), nullable=True, primary_key=False),
        sa.Column("input_schema", sa.JSON(), nullable=True, primary_key=False, default=dict),
        sa.Column("metadata_json", sa.JSON(), nullable=True, primary_key=False, default=dict),
        sa.Column("enabled", sa.Boolean(), nullable=False, primary_key=False, default=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True, primary_key=False),
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
        sa.ForeignKeyConstraint(["server_id"], ["mcp_servers.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
        sa.UniqueConstraint("server_id", "name", name="uq_mcp_tools_server_name"),
    )
