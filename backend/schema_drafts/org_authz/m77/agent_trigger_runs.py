"""Frozen m77 agent_trigger_runs fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "agent_trigger_runs",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("trigger_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, primary_key=False, default="running"
        ),
        sa.Column(
            "source", sa.String(length=20), nullable=False, primary_key=False, default="scheduled"
        ),
        sa.Column("input_message", sa.Text(), nullable=False, primary_key=False),
        sa.Column("error_message", sa.Text(), nullable=True, primary_key=False),
        sa.Column("output_preview", sa.Text(), nullable=True, primary_key=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("thread_id", sa.String(length=80), nullable=True, primary_key=False),
        sa.Column("checkpoint_id", sa.String(length=80), nullable=True, primary_key=False),
        sa.Column("trace_id", sa.String(length=80), nullable=True, primary_key=False),
        sa.Column("identity_mode", sa.String(length=20), nullable=True, primary_key=False),
        sa.Column("agent_runtime_name", sa.String(length=40), nullable=True, primary_key=False),
        sa.Column("credential_subject_user_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column(
            "started_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.Column("finished_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["trigger_id"], ["agent_triggers.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["credential_subject_user_id"], ["users.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name=None, ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
    )
