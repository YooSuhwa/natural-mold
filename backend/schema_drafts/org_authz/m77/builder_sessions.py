"""Frozen m77 builder_sessions fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "builder_sessions",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("user_request", sa.Text(), nullable=False, primary_key=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, primary_key=False, default="building"
        ),
        sa.Column("current_phase", sa.Integer(), nullable=False, primary_key=False, default=0),
        sa.Column(
            "project_path", sa.String(length=500), nullable=False, primary_key=False, default=""
        ),
        sa.Column("intent", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("tools_result", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("middlewares_result", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("system_prompt", sa.Text(), nullable=True, primary_key=False),
        sa.Column("draft_config", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("agent_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("error_message", sa.Text(), nullable=True, primary_key=False),
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
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=None, ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
    )
