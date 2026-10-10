"""Frozen m77 agent_api_keys fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "agent_api_keys",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("name", sa.String(length=100), nullable=False, primary_key=False),
        sa.Column("description", sa.Text(), nullable=True, primary_key=False),
        sa.Column("key_id", sa.String(length=40), nullable=False, primary_key=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("prefix", sa.String(length=80), nullable=False, primary_key=False),
        sa.Column("last_four", sa.String(length=4), nullable=False, primary_key=False),
        sa.Column("scopes", sa.JSON(), nullable=False, primary_key=False, default=list),
        sa.Column(
            "allow_all_deployments", sa.Boolean(), nullable=False, primary_key=False, default=False
        ),
        sa.Column("revoked_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("usage_count", sa.Integer(), nullable=False, primary_key=False, default=0),
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
        sa.PrimaryKeyConstraint("id", name=None),
        sa.UniqueConstraint("key_id", name="uq_agent_api_keys_key_id"),
    )
    sa.Index(
        "ix_agent_api_keys_user_id", metadata.tables["agent_api_keys"].c["user_id"], unique=False
    )
