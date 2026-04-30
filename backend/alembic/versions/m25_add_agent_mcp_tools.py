"""M25: ``agent_mcp_tools`` link table — completes the m5 follow-up so agents
can finally bind MCP-imported tools alongside regular Tool rows.

Revision ID: m25_add_agent_mcp_tools
Revises: m24_add_credential_is_system
Create Date: 2026-04-30

Until now ``agent_tools`` referenced ``tools.id`` (user-instantiated rows)
only, and ``chat_service.build_tools_config`` skipped MCP tools entirely.
The unified Tools-and-Skills dialog (4-tab) needs MCP rows to be
selectable per agent, so we materialize the link as its own table:

  ``(agent_id, mcp_tool_id)`` composite PK, both FKs cascade on delete.

The MCP server's transport / credential live on ``mcp_servers``; runtime
joins the row when emitting executor configs.

Reversible.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m25_add_agent_mcp_tools"
down_revision = "m24_add_credential_is_system"
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table in inspector.get_table_names()


def upgrade() -> None:
    if _has_table("agent_mcp_tools"):
        return
    op.create_table(
        "agent_mcp_tools",
        sa.Column(
            "agent_id",
            sa.UUID(),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "mcp_tool_id",
            sa.UUID(),
            sa.ForeignKey("mcp_tools.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    if _has_table("agent_mcp_tools"):
        op.drop_table("agent_mcp_tools")
