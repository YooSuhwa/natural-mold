"""Persist bounded MCP Apps metadata and invocation provenance."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m75_mcp_apps_provenance"
down_revision = "m74_conversation_run_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mcp_tools", sa.Column("metadata_json", sa.JSON(), nullable=True))
    op.create_table(
        "mcp_app_invocation_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("credential_subject_user_id", sa.Uuid(), nullable=False),
        sa.Column("mcp_server_id", sa.Uuid(), nullable=False),
        sa.Column("mcp_tool_id", sa.Uuid(), nullable=False),
        sa.Column("tool_call_id", sa.String(255), nullable=False),
        sa.Column("remote_tool_name", sa.String(150), nullable=False),
        sa.Column("resource_uri", sa.String(1_000), nullable=False),
        sa.Column("tool_meta", sa.JSON(), nullable=False),
        sa.Column("result_meta", sa.JSON(), nullable=True),
        sa.Column("structured_content", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["credential_subject_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mcp_server_id"], ["mcp_servers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mcp_tool_id"], ["mcp_tools.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["conversation_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "tool_call_id", name="uq_mcp_app_binding_run_tool_call"),
    )
    op.create_index(
        "ix_mcp_app_binding_owner_run",
        "mcp_app_invocation_bindings",
        ["user_id", "conversation_id", "run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_mcp_app_binding_owner_run", table_name="mcp_app_invocation_bindings")
    op.drop_table("mcp_app_invocation_bindings")
    op.drop_column("mcp_tools", "metadata_json")
