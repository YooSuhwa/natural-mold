"""Frozen m77 agent_triggers fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "agent_triggers",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("agent_id", sa.Uuid(), nullable=False, primary_key=False, index=True),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column(
            "name", sa.String(length=120), nullable=False, primary_key=False, default="스케줄"
        ),
        sa.Column("trigger_type", sa.String(length=20), nullable=False, primary_key=False),
        sa.Column("schedule_config", sa.JSON(), nullable=False, primary_key=False),
        sa.Column("input_message", sa.Text(), nullable=False, primary_key=False),
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            primary_key=False,
            default="Asia/Seoul",
        ),
        sa.Column(
            "conversation_policy",
            sa.String(length=40),
            nullable=False,
            primary_key=False,
            default="schedule_thread",
        ),
        sa.Column("schedule_conversation_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("target_conversation_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, primary_key=False, default="active"
        ),
        sa.Column("last_run_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("next_run_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("last_status", sa.String(length=20), nullable=True, primary_key=False),
        sa.Column("last_error", sa.Text(), nullable=True, primary_key=False),
        sa.Column("run_count", sa.Integer(), nullable=False, primary_key=False, default=0),
        sa.Column("failure_count", sa.Integer(), nullable=False, primary_key=False, default=0),
        sa.Column("max_runs", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("end_at", sa.DateTime(), nullable=True, primary_key=False),
        sa.Column("auto_pause_after_failures", sa.Integer(), nullable=True, primary_key=False),
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
            ["target_conversation_id"], ["conversations.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["schedule_conversation_id"], ["conversations.id"], name=None, ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
    )
