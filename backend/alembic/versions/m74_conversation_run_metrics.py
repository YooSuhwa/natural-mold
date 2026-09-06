"""Add durable per-run metrics and token-usage association."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m74_conversation_run_metrics"
down_revision = "m73_conversation_run_inputs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_run_metrics",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("terminal_state", sa.String(20), nullable=True),
        sa.Column("elapsed_ms", sa.Float(), nullable=True),
        sa.Column("ttft_ms", sa.Float(), nullable=True),
        sa.Column("generation_ms", sa.Float(), nullable=True),
        sa.Column("tokens_per_second", sa.Float(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(18, 8), nullable=True),
        sa.Column("usage_complete", sa.Boolean(), nullable=False),
        sa.Column("root_tool_calls", sa.Integer(), nullable=True),
        sa.Column("descendant_tool_calls", sa.Integer(), nullable=True),
        sa.Column("root_subagent_calls", sa.Integer(), nullable=True),
        sa.Column("descendant_subagent_calls", sa.Integer(), nullable=True),
        sa.Column("activity_json", sa.JSON(), nullable=False),
        sa.Column("activity_truncated", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["conversation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.add_column("token_usages", sa.Column("run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_token_usages_run_id",
        "token_usages",
        "conversation_runs",
        ["run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint("uq_token_usages_run_id", "token_usages", ["run_id"])


def downgrade() -> None:
    op.drop_constraint("uq_token_usages_run_id", "token_usages", type_="unique")
    op.drop_constraint("fk_token_usages_run_id", "token_usages", type_="foreignkey")
    op.drop_column("token_usages", "run_id")
    op.drop_table("conversation_run_metrics")
