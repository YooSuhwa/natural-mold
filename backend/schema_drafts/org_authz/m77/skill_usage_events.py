"""Frozen m77 skill_usage_events fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "skill_usage_events",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("skill_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("source_kind", sa.String(length=30), nullable=False, primary_key=False),
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("agent_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("model_name", sa.String(length=160), nullable=True, primary_key=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False, primary_key=False, default=0),
        sa.Column("tokens_out", sa.Integer(), nullable=False, primary_key=False, default=0),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=True, primary_key=False),
        sa.Column("execution_count", sa.Integer(), nullable=False, primary_key=False, default=1),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=None, ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"], ["skill_evaluation_runs.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name=None, ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
    )
    sa.Index(
        "ix_skill_usage_events_conversation",
        metadata.tables["skill_usage_events"].c["conversation_id"],
        unique=False,
    )
    sa.Index(
        "ix_skill_usage_events_evaluation_run",
        metadata.tables["skill_usage_events"].c["evaluation_run_id"],
        unique=False,
    )
    sa.Index(
        "ix_skill_usage_events_skill_created",
        metadata.tables["skill_usage_events"].c["skill_id"],
        metadata.tables["skill_usage_events"].c["created_at"],
        unique=False,
    )
    sa.Index(
        "ix_skill_usage_events_user",
        metadata.tables["skill_usage_events"].c["user_id"],
        unique=False,
    )
