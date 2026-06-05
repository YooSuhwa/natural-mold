"""M56: memory policy and records.

Revision ID: m56_memory_policy
Revises: m55_user_profile_personalization
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m56_memory_policy"
down_revision = "m55_user_profile_personalization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_memory_settings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("memory_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "memory_read_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "memory_write_policy",
            sa.String(length=20),
            nullable=False,
            server_default="ask",
        ),
        sa.Column("allowed_scopes", sa.String(length=20), nullable=False, server_default="both"),
        sa.Column(
            "trigger_memory_write_policy",
            sa.String(length=20),
            nullable=False,
            server_default="off",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "memory_write_policy in ('off', 'ask', 'auto')",
            name="ck_user_memory_settings_write_policy",
        ),
        sa.CheckConstraint(
            "allowed_scopes in ('user', 'agent', 'both')",
            name="ck_user_memory_settings_allowed_scopes",
        ),
        sa.CheckConstraint(
            "trigger_memory_write_policy in ('off', 'auto')",
            name="ck_user_memory_settings_trigger_policy",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "agent_memory_settings",
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column(
            "memory_policy_override",
            sa.String(length=20),
            nullable=False,
            server_default="inherit",
        ),
        sa.Column(
            "memory_scopes_override",
            sa.String(length=20),
            nullable=False,
            server_default="inherit",
        ),
        sa.Column(
            "trigger_memory_policy_override",
            sa.String(length=20),
            nullable=False,
            server_default="inherit",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "memory_policy_override in ('inherit', 'off', 'ask', 'auto')",
            name="ck_agent_memory_settings_policy_override",
        ),
        sa.CheckConstraint(
            "memory_scopes_override in ('inherit', 'agent_only', 'user_and_agent')",
            name="ck_agent_memory_settings_scopes_override",
        ),
        sa.CheckConstraint(
            "trigger_memory_policy_override in ('inherit', 'off', 'auto')",
            name="ck_agent_memory_settings_trigger_override",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_id"),
    )

    op.create_table(
        "memory_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("store_path", sa.String(length=200), nullable=False),
        sa.Column("source_conversation_id", sa.Uuid(), nullable=True),
        sa.Column("source_message_id", sa.String(length=128), nullable=True),
        sa.Column("source_run_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("scope in ('user', 'agent')", name="ck_memory_records_scope"),
        sa.CheckConstraint(
            "status in ('active', 'deleted')",
            name="ck_memory_records_status",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_conversation_id"],
            ["conversations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_records_user_id", "memory_records", ["user_id"])
    op.create_index("ix_memory_records_agent_id", "memory_records", ["agent_id"])
    op.create_index(
        "ix_memory_records_source_conversation_id",
        "memory_records",
        ["source_conversation_id"],
    )
    op.create_index(
        "ix_memory_records_user_scope_status",
        "memory_records",
        ["user_id", "scope", "status"],
    )

    op.create_table(
        "memory_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("source_run_id", sa.String(length=128), nullable=True),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("scope in ('user', 'agent')", name="ck_memory_proposals_scope"),
        sa.CheckConstraint(
            "status in ('pending', 'approved', 'rejected', 'expired')",
            name="ck_memory_proposals_status",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_proposals_user_id", "memory_proposals", ["user_id"])
    op.create_index("ix_memory_proposals_agent_id", "memory_proposals", ["agent_id"])
    op.create_index(
        "ix_memory_proposals_conversation_id",
        "memory_proposals",
        ["conversation_id"],
    )
    op.create_index(
        "ix_memory_proposals_user_status",
        "memory_proposals",
        ["user_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_memory_proposals_user_status", table_name="memory_proposals")
    op.drop_index("ix_memory_proposals_conversation_id", table_name="memory_proposals")
    op.drop_index("ix_memory_proposals_agent_id", table_name="memory_proposals")
    op.drop_index("ix_memory_proposals_user_id", table_name="memory_proposals")
    op.drop_table("memory_proposals")

    op.drop_index("ix_memory_records_user_scope_status", table_name="memory_records")
    op.drop_index("ix_memory_records_source_conversation_id", table_name="memory_records")
    op.drop_index("ix_memory_records_agent_id", table_name="memory_records")
    op.drop_index("ix_memory_records_user_id", table_name="memory_records")
    op.drop_table("memory_records")

    op.drop_table("agent_memory_settings")
    op.drop_table("user_memory_settings")
