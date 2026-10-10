"""Frozen m77 agent_deployments fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "agent_deployments",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("agent_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("public_id", sa.String(length=80), nullable=False, primary_key=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, primary_key=False, default="active"
        ),
        sa.Column("allow_streaming", sa.Boolean(), nullable=False, primary_key=False, default=True),
        sa.Column(
            "allow_background", sa.Boolean(), nullable=False, primary_key=False, default=False
        ),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("daily_token_limit", sa.Integer(), nullable=True, primary_key=False),
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
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
        sa.UniqueConstraint("agent_id", name="uq_agent_deployments_agent_id"),
        sa.UniqueConstraint("public_id", name="uq_agent_deployments_public_id"),
    )
    sa.Index(
        "ix_agent_deployments_status",
        metadata.tables["agent_deployments"].c["status"],
        unique=False,
    )
    sa.Index(
        "ix_agent_deployments_user_id",
        metadata.tables["agent_deployments"].c["user_id"],
        unique=False,
    )
