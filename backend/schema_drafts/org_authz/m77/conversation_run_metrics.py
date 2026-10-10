"""Frozen m77 conversation_run_metrics fixture definition; do not derive from live ORM."""

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "conversation_run_metrics",
        metadata,
        sa.Column("run_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("terminal_state", sa.String(length=20), nullable=True, primary_key=False),
        sa.Column("elapsed_ms", sa.Float(), nullable=True, primary_key=False),
        sa.Column("ttft_ms", sa.Float(), nullable=True, primary_key=False),
        sa.Column("generation_ms", sa.Float(), nullable=True, primary_key=False),
        sa.Column("tokens_per_second", sa.Float(), nullable=True, primary_key=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True, primary_key=False),
        sa.Column(
            "estimated_cost", sa.Numeric(precision=18, scale=8), nullable=True, primary_key=False
        ),
        sa.Column("usage_complete", sa.Boolean(), nullable=False, primary_key=False, default=False),
        sa.Column("root_tool_calls", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("descendant_tool_calls", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("root_subagent_calls", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("descendant_subagent_calls", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("activity_json", sa.JSON(), nullable=False, primary_key=False, default=list),
        sa.Column(
            "activity_truncated", sa.Boolean(), nullable=False, primary_key=False, default=False
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
        sa.ForeignKeyConstraint(
            ["run_id"], ["conversation_runs.id"], name=None, ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("run_id", name=None),
    )
