"""Frozen m77 system_llm_settings fixture definition; do not derive from live ORM."""

from uuid import uuid4

import sqlalchemy as sa

from . import defaults


def register(metadata: sa.MetaData) -> None:
    sa.Table(
        "system_llm_settings",
        metadata,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True, default=uuid4),
        sa.Column("role", sa.String(length=40), nullable=False, primary_key=False, unique=True),
        sa.Column("credential_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("model_name", sa.String(length=200), nullable=True, primary_key=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            primary_key=False,
            default=defaults.now_naive,
            onupdate=defaults.now_naive,
        ),
        sa.CheckConstraint(
            "role IN ('text_primary', 'text_fallback', 'image')", name="ck_system_llm_settings_role"
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["credentials.id"], name=None, ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=None),
    )
