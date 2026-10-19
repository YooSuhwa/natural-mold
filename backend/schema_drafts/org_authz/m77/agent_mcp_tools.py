"""Frozen m77 agent_mcp_tools fixture definition; do not derive from live ORM."""

import sqlalchemy as sa


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "agent_mcp_tools",
        metadata,
        sa.Column("agent_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("mcp_tool_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mcp_tool_id"], ["mcp_tools.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_id", "mcp_tool_id", name=None),
    )
