"""Frozen m77 templates fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "templates",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("name", sa.String(length=100), nullable=False, primary_key=False),
        sa.Column("description", sa.Text(), nullable=True, primary_key=False),
        sa.Column("category", sa.String(length=50), nullable=False, primary_key=False),
        sa.Column("system_prompt", sa.Text(), nullable=False, primary_key=False),
        sa.Column("recommended_tools", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("recommended_skill_slugs", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("recommended_model_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("usage_example", sa.Text(), nullable=True, primary_key=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.PrimaryKeyConstraint("id", name=None),
    )
