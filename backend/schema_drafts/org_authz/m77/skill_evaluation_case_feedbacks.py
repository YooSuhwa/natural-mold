"""Frozen m77 skill_evaluation_case_feedbacks fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "skill_evaluation_case_feedbacks",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("run_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("case_index", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("verdict", sa.String(length=10), nullable=False, primary_key=False),
        sa.Column("comment", sa.Text(), nullable=True, primary_key=False),
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
            ["run_id"], ["skill_evaluation_runs.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
        sa.UniqueConstraint(
            "run_id", "user_id", "case_index", name="uq_skill_eval_case_feedback_run_user_case"
        ),
    )
