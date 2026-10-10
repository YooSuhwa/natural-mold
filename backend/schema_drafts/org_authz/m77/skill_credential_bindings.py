"""Frozen m77 skill_credential_bindings fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "skill_credential_bindings",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("skill_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("requirement_key", sa.String(length=120), nullable=False, primary_key=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column(
            "scope", sa.String(length=20), nullable=False, primary_key=False, default="skill"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
            onupdate=defaults.now_naive,
        ),
        sa.CheckConstraint(
            "scope IN ('skill','agent_skill')", name="ck_skill_credential_binding_scope"
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["credentials.id"], name=None, ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], name=None, ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=None, ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=None),
        sa.UniqueConstraint(
            "skill_id", "user_id", "requirement_key", "scope", name="uq_skill_credential_binding"
        ),
    )
