"""Frozen m77 skill_evaluation_runs fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "skill_evaluation_runs",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("evaluation_set_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            primary_key=False,
            default="queued",
            server_default="queued",
        ),
        sa.Column("skill_version", sa.String(length=40), nullable=True, primary_key=False),
        sa.Column("skill_content_hash", sa.String(length=64), nullable=True, primary_key=False),
        sa.Column("runner_model", sa.String(length=160), nullable=True, primary_key=False),
        sa.Column(
            "runner_version",
            sa.String(length=40),
            nullable=False,
            primary_key=False,
            default="1",
            server_default="1",
        ),
        sa.Column(
            "grader_prompt_version",
            sa.String(length=40),
            nullable=False,
            primary_key=False,
            default="1",
            server_default="1",
        ),
        sa.Column(
            "eval_schema_version",
            sa.Integer(),
            nullable=False,
            primary_key=False,
            default=1,
            server_default="1",
        ),
        sa.Column("run_config", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("estimate", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("summary", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("benchmark", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("usage", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("case_results", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("artifact_path", sa.String(length=500), nullable=True, primary_key=False),
        sa.Column("error_message", sa.Text(), nullable=True, primary_key=False),
        sa.Column("cancellation_requested_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("cancellation_reason", sa.String(length=120), nullable=True, primary_key=False),
        sa.Column("started_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True, primary_key=False),
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
            ["evaluation_set_id"], ["skill_evaluation_sets.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
    )
    sa.Index(
        "ix_skill_evaluation_runs_set_created",
        metadata.tables["skill_evaluation_runs"].c["evaluation_set_id"],
        metadata.tables["skill_evaluation_runs"].c["created_at"],
        unique=False,
    )
    sa.Index(
        "ix_skill_evaluation_runs_skill_created",
        metadata.tables["skill_evaluation_runs"].c["skill_id"],
        metadata.tables["skill_evaluation_runs"].c["created_at"],
        unique=False,
    )
    sa.Index(
        "ix_skill_evaluation_runs_status",
        metadata.tables["skill_evaluation_runs"].c["status"],
        unique=False,
    )
