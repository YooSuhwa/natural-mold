"""Skill-axis usage ledger + human feedback tables + measured run usage (Phase 3).

* ``skill_usage_events`` — honest skill-axis usage: real evaluation-run LLM
  tokens/cost (fully attributable to one skill) and chat ``execute_in_skill``
  execution counts. No whole-conversation cost attribution (D3).
* ``skill_evaluation_runs.usage`` — measured per-run rollup
  ``{model_calls, tokens_in, tokens_out, cost_usd, measured}``.
* ``skill_feedbacks`` — per-skill human rating (up/down + comment).
* ``skill_evaluation_case_feedbacks`` — per-case grader-verdict feedback
  (agree/disagree + comment), display-only in v1.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m70_skill_usage_and_feedback"
down_revision = "m69_skill_builder_sessions_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_usage_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_kind", sa.String(length=30), nullable=False),
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("model_name", sa.String(length=160), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("execution_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            ["skill_evaluation_runs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_skill_usage_events_skill_created",
        "skill_usage_events",
        ["skill_id", "created_at"],
    )
    op.create_index(
        "ix_skill_usage_events_evaluation_run",
        "skill_usage_events",
        ["evaluation_run_id"],
    )

    op.add_column("skill_evaluation_runs", sa.Column("usage", sa.JSON(), nullable=True))

    op.create_table(
        "skill_feedbacks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.String(length=8), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("skill_id", "user_id", name="uq_skill_feedback_skill_user"),
    )

    op.create_table(
        "skill_evaluation_case_feedbacks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("case_index", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(length=10), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["run_id"], ["skill_evaluation_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "user_id",
            "case_index",
            name="uq_skill_eval_case_feedback_run_user_case",
        ),
    )


def downgrade() -> None:
    op.drop_table("skill_evaluation_case_feedbacks")
    op.drop_table("skill_feedbacks")
    op.drop_column("skill_evaluation_runs", "usage")
    op.drop_index("ix_skill_usage_events_evaluation_run", table_name="skill_usage_events")
    op.drop_index("ix_skill_usage_events_skill_created", table_name="skill_usage_events")
    op.drop_table("skill_usage_events")
