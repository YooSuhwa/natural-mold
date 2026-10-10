"""Frozen m77 agent_skills fixture definition; do not derive from live ORM."""

import sqlalchemy as sa


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "agent_skills",
        metadata,
        sa.Column("agent_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("skill_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("config", sa.JSON(), nullable=True, primary_key=False),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("agent_id", "skill_id", name=None),
    )
