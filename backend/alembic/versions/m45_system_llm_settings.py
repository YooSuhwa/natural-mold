"""M45: ``system_llm_settings`` table (ADR-019).

Revision ID: m45_system_llm_settings
Revises: m44_relative_storage_path
Create Date: 2026-05-26

Operator-selected LLM model per role (text_primary / text_fallback / image).
Replaces the ``.env`` hardcoding of system feature models. Seeds three role
rows with NULL credential/model — the operator picks via the System LLM
settings screen. Reversible (downgrade drops the table).
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from alembic import op

revision = "m45_system_llm_settings"
down_revision = "m44_relative_storage_path"
branch_labels = None
depends_on = None

_ROLES = ("text_primary", "text_fallback", "image")


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("system_llm_settings"):
        table = op.create_table(
            "system_llm_settings",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("role", sa.String(40), nullable=False),
            sa.Column(
                "credential_id",
                sa.Uuid(),
                # SET NULL — deleting the credential transitions the slot to an
                # unconfigured state, surfacing a clear error on next use.
                sa.ForeignKey("credentials.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("model_name", sa.String(200), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint(
                "role IN ('text_primary', 'text_fallback', 'image')",
                name="ck_system_llm_settings_role",
            ),
            sa.UniqueConstraint("role", name="uq_system_llm_settings_role"),
        )
        # Idempotent seed: one row per role with NULL credential/model.
        op.bulk_insert(
            table,
            [
                {
                    "id": uuid.uuid4(),
                    "role": role,
                    "credential_id": None,
                    "model_name": None,
                }
                for role in _ROLES
            ],
        )


def downgrade() -> None:
    if _has_table("system_llm_settings"):
        op.drop_table("system_llm_settings")
