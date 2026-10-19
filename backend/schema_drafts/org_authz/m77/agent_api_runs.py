"""Frozen m77 agent_api_runs fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "agent_api_runs",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column(
            "public_id", sa.String(length=80), nullable=False, primary_key=False, unique=True
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("api_key_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("deployment_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("thread_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("mode", sa.String(length=20), nullable=False, primary_key=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, primary_key=False, default="pending"
        ),
        sa.Column("error_code", sa.String(length=80), nullable=True, primary_key=False),
        sa.Column("error_message", sa.Text(), nullable=True, primary_key=False),
        sa.Column("input", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("output", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("metadata", sa.JSON(), nullable=True, primary_key=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["agent_api_threads.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"], ["agent_deployments.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["api_key_id"], ["agent_api_keys.id"], name=None, ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
    )
    sa.Index(
        "ix_agent_api_runs_deployment_id",
        metadata.tables["agent_api_runs"].c["deployment_id"],
        unique=False,
    )
    sa.Index(
        "ix_agent_api_runs_thread_id",
        metadata.tables["agent_api_runs"].c["thread_id"],
        unique=False,
    )
    sa.Index(
        "ix_agent_api_runs_user_id", metadata.tables["agent_api_runs"].c["user_id"], unique=False
    )
