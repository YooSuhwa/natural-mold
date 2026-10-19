"""Frozen m77 agent_subagents fixture definition; do not derive from live ORM."""

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "agent_subagents",
        metadata,
        sa.Column("parent_agent_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("sub_agent_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False, primary_key=False, default=0),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.CheckConstraint("parent_agent_id != sub_agent_id", name="ck_agent_subagents_no_self"),
        sa.ForeignKeyConstraint(["parent_agent_id"], ["agents.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sub_agent_id"], ["agents.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("parent_agent_id", "sub_agent_id", name=None),
    )
    sa.Index(
        "ix_agent_subagents_parent_agent_id",
        metadata.tables["agent_subagents"].c["parent_agent_id"],
        unique=False,
    )
