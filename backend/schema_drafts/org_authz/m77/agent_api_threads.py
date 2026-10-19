"""Frozen m77 agent_api_threads fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "agent_api_threads",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("public_id", sa.String(length=80), nullable=False, primary_key=False),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("deployment_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("external_user", sa.String(length=200), nullable=True, primary_key=False),
        sa.Column("metadata", sa.JSON(), nullable=True, primary_key=False),
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
        sa.ForeignKeyConstraint(
            ["deployment_id"], ["agent_deployments.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name=None, ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
        sa.UniqueConstraint("public_id", name="uq_agent_api_threads_public_id"),
    )
    sa.Index(
        "ix_agent_api_threads_deployment_id",
        metadata.tables["agent_api_threads"].c["deployment_id"],
        unique=False,
    )
    sa.Index(
        "ix_agent_api_threads_user_id",
        metadata.tables["agent_api_threads"].c["user_id"],
        unique=False,
    )
