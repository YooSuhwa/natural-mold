"""Frozen m77 mcp_app_invocation_bindings fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "mcp_app_invocation_bindings",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("run_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("credential_subject_user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("mcp_server_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("mcp_tool_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("tool_call_id", sa.String(length=255), nullable=False, primary_key=False),
        sa.Column("remote_tool_name", sa.String(length=150), nullable=False, primary_key=False),
        sa.Column("resource_uri", sa.String(length=1000), nullable=False, primary_key=False),
        sa.Column("tool_meta", sa.JSON(), nullable=False, primary_key=False),
        sa.Column("result_meta", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("structured_content", sa.JSON(), nullable=True, primary_key=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["conversation_runs.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mcp_server_id"], ["mcp_servers.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["mcp_tool_id"], ["mcp_tools.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["credential_subject_user_id"], ["users.id"], name=None, ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name=None, ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
        sa.UniqueConstraint("run_id", "tool_call_id", name="uq_mcp_app_binding_run_tool_call"),
    )
    sa.Index(
        "ix_mcp_app_binding_owner_run",
        metadata.tables["mcp_app_invocation_bindings"].c["user_id"],
        metadata.tables["mcp_app_invocation_bindings"].c["conversation_id"],
        metadata.tables["mcp_app_invocation_bindings"].c["run_id"],
        unique=False,
    )
