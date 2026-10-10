"""Frozen m77 token_usages fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "token_usages",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("run_id", sa.Uuid(), nullable=True, primary_key=False, unique=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=False, primary_key=False, index=True),
        sa.Column("agent_id", sa.Uuid(), nullable=False, primary_key=False, index=True),
        sa.Column("model_name", sa.String(length=100), nullable=False, primary_key=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False, primary_key=False),
        sa.Column(
            "estimated_cost", sa.Numeric(precision=10, scale=6), nullable=True, primary_key=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["conversation_runs.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
    )
