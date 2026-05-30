"""M47: productize agent schedules with run history and unread state.

Revision ID: m47_schedule_productization
Revises: m46_models_is_visible
Create Date: 2026-05-30
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m47_schedule_productization"
down_revision = "m46_models_is_visible"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("unread_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("conversations", sa.Column("last_read_at", sa.DateTime(), nullable=True))
    op.add_column("conversations", sa.Column("last_unread_at", sa.DateTime(), nullable=True))
    op.add_column(
        "conversations",
        sa.Column(
            "last_activity_source",
            sa.String(length=20),
            nullable=False,
            server_default="user",
        ),
    )

    op.add_column(
        "agent_triggers",
        sa.Column("name", sa.String(length=120), nullable=False, server_default="스케줄"),
    )
    op.add_column(
        "agent_triggers",
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            server_default="Asia/Seoul",
        ),
    )
    op.add_column(
        "agent_triggers",
        sa.Column(
            "conversation_policy",
            sa.String(length=40),
            nullable=False,
            server_default="schedule_thread",
        ),
    )
    op.add_column(
        "agent_triggers",
        sa.Column("schedule_conversation_id", sa.Uuid(), nullable=True),
    )
    op.add_column("agent_triggers", sa.Column("last_status", sa.String(length=20), nullable=True))
    op.add_column("agent_triggers", sa.Column("last_error", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_agent_triggers_schedule_conversation_id_conversations",
        "agent_triggers",
        "conversations",
        ["schedule_conversation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_agent_triggers_user_status",
        "agent_triggers",
        ["user_id", "status"],
    )

    op.create_table(
        "agent_trigger_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trigger_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("input_message", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["trigger_id"], ["agent_triggers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_trigger_runs_trigger_started",
        "agent_trigger_runs",
        ["trigger_id", "started_at"],
    )
    op.create_index(
        "ix_agent_trigger_runs_user_started",
        "agent_trigger_runs",
        ["user_id", "started_at"],
    )

    op.alter_column("conversations", "unread_count", server_default=None)
    op.alter_column("conversations", "last_activity_source", server_default=None)
    op.alter_column("agent_triggers", "name", server_default=None)
    op.alter_column("agent_triggers", "timezone", server_default=None)
    op.alter_column("agent_triggers", "conversation_policy", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_agent_trigger_runs_user_started", table_name="agent_trigger_runs")
    op.drop_index("ix_agent_trigger_runs_trigger_started", table_name="agent_trigger_runs")
    op.drop_table("agent_trigger_runs")
    op.drop_index("ix_agent_triggers_user_status", table_name="agent_triggers")
    op.drop_constraint(
        "fk_agent_triggers_schedule_conversation_id_conversations",
        "agent_triggers",
        type_="foreignkey",
    )
    op.drop_column("agent_triggers", "last_error")
    op.drop_column("agent_triggers", "last_status")
    op.drop_column("agent_triggers", "schedule_conversation_id")
    op.drop_column("agent_triggers", "conversation_policy")
    op.drop_column("agent_triggers", "timezone")
    op.drop_column("agent_triggers", "name")
    op.drop_column("conversations", "last_activity_source")
    op.drop_column("conversations", "last_unread_at")
    op.drop_column("conversations", "last_read_at")
    op.drop_column("conversations", "unread_count")
