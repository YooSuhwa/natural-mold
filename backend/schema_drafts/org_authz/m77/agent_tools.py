"""Frozen m77 agent_tools fixture definition; do not derive from live ORM."""

import sqlalchemy as sa


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "agent_tools",
        metadata,
        sa.Column("agent_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("tool_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tool_id"], ["tools.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_id", "tool_id", name=None),
    )
