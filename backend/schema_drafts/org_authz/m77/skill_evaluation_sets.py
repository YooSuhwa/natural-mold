"""Frozen m77 skill_evaluation_sets fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "skill_evaluation_sets",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("name", sa.String(length=160), nullable=False, primary_key=False),
        sa.Column("description", sa.Text(), nullable=True, primary_key=False),
        sa.Column(
            "source_kind",
            sa.String(length=40),
            nullable=False,
            primary_key=False,
            default="builder",
            server_default="builder",
        ),
        sa.Column("template_key", sa.String(length=80), nullable=True, primary_key=False),
        sa.Column("template_version", sa.String(length=40), nullable=True, primary_key=False),
        sa.Column("generation_strategy", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("evals", sa.JSON(), nullable=False, primary_key=False),
        sa.Column(
            "expectations_schema_version",
            sa.Integer(),
            nullable=False,
            primary_key=False,
            default=1,
            server_default="1",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
            onupdate=defaults.now_naive,
        ),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
    )
    sa.Index(
        "ix_skill_evaluation_sets_skill_updated",
        metadata.tables["skill_evaluation_sets"].c["skill_id"],
        metadata.tables["skill_evaluation_sets"].c["updated_at"],
        unique=False,
    )
