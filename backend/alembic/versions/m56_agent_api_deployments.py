"""M56: agent api deployments and keys.

Revision ID: m56_agent_api_deployments
Revises: m55_user_profile_personalization
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m56_agent_api_deployments"
down_revision = "m55_user_profile_personalization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("source", sa.String(length=20), nullable=False, server_default="ui"),
    )
    op.create_index("ix_conversations_source", "conversations", ["source"])

    op.create_table(
        "agent_deployments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("allow_streaming", sa.Boolean(), nullable=False),
        sa.Column("allow_background", sa.Boolean(), nullable=False),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=True),
        sa.Column("daily_token_limit", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", name="uq_agent_deployments_agent_id"),
        sa.UniqueConstraint("public_id", name="uq_agent_deployments_public_id"),
    )
    op.create_index("ix_agent_deployments_status", "agent_deployments", ["status"])
    op.create_index("ix_agent_deployments_user_id", "agent_deployments", ["user_id"])

    op.create_table(
        "agent_api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("key_id", sa.String(length=40), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=80), nullable=False),
        sa.Column("last_four", sa.String(length=4), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("allow_all_deployments", sa.Boolean(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("usage_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_id", name="uq_agent_api_keys_key_id"),
    )
    op.create_index("ix_agent_api_keys_user_id", "agent_api_keys", ["user_id"])

    op.create_table(
        "agent_api_key_deployments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("api_key_id", sa.Uuid(), nullable=False),
        sa.Column("deployment_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["api_key_id"], ["agent_api_keys.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deployment_id"], ["agent_deployments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "api_key_id", "deployment_id", name="uq_agent_api_key_deployment"
        ),
    )
    op.create_index(
        "ix_agent_api_key_deployments_deployment_id",
        "agent_api_key_deployments",
        ["deployment_id"],
    )

    op.create_table(
        "agent_api_threads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(length=80), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("deployment_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("external_user", sa.String(length=200), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deployment_id"], ["agent_deployments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id", name="uq_agent_api_threads_public_id"),
    )
    op.create_index("ix_agent_api_threads_deployment_id", "agent_api_threads", ["deployment_id"])
    op.create_index("ix_agent_api_threads_user_id", "agent_api_threads", ["user_id"])

    op.create_table(
        "agent_api_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.String(length=80), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("api_key_id", sa.Uuid(), nullable=True),
        sa.Column("deployment_id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("input", sa.JSON(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["api_key_id"], ["agent_api_keys.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deployment_id"], ["agent_deployments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id"], ["agent_api_threads.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index("ix_agent_api_runs_deployment_id", "agent_api_runs", ["deployment_id"])
    op.create_index("ix_agent_api_runs_thread_id", "agent_api_runs", ["thread_id"])
    op.create_index("ix_agent_api_runs_user_id", "agent_api_runs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_api_runs_user_id", table_name="agent_api_runs")
    op.drop_index("ix_agent_api_runs_thread_id", table_name="agent_api_runs")
    op.drop_index("ix_agent_api_runs_deployment_id", table_name="agent_api_runs")
    op.drop_table("agent_api_runs")
    op.drop_index("ix_agent_api_threads_user_id", table_name="agent_api_threads")
    op.drop_index("ix_agent_api_threads_deployment_id", table_name="agent_api_threads")
    op.drop_table("agent_api_threads")
    op.drop_index(
        "ix_agent_api_key_deployments_deployment_id",
        table_name="agent_api_key_deployments",
    )
    op.drop_table("agent_api_key_deployments")
    op.drop_index("ix_agent_api_keys_user_id", table_name="agent_api_keys")
    op.drop_table("agent_api_keys")
    op.drop_index("ix_agent_deployments_user_id", table_name="agent_deployments")
    op.drop_index("ix_agent_deployments_status", table_name="agent_deployments")
    op.drop_table("agent_deployments")
    op.drop_index("ix_conversations_source", table_name="conversations")
    op.drop_column("conversations", "source")
