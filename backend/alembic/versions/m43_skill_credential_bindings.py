"""M43: ``skill_credential_bindings`` table (ADR-017 Slice A).

Revision ID: m43_skill_credential_bindings
Revises: m42_agent_skills_config
Create Date: 2026-05-18

Spec §3.9 — per-user binding between a skill's ``credential_requirements``
entries and concrete ``credentials`` rows. ON DELETE RESTRICT on
``credential_id`` prevents accidental orphaning of a running binding;
operators must clear the binding first.

Reversible.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m43_skill_credential_bindings"
down_revision = "m42_agent_skills_config"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("skill_credential_bindings"):
        op.create_table(
            "skill_credential_bindings",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "skill_id",
                sa.Uuid(),
                sa.ForeignKey("skills.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("requirement_key", sa.String(120), nullable=False),
            sa.Column(
                "credential_id",
                sa.Uuid(),
                # RESTRICT — credential is in active use; require explicit
                # binding removal before deletion.
                sa.ForeignKey("credentials.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "scope",
                sa.String(20),
                nullable=False,
                server_default="skill",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint(
                "scope IN ('skill','agent_skill')",
                name="ck_skill_credential_binding_scope",
            ),
            sa.UniqueConstraint(
                "skill_id",
                "user_id",
                "requirement_key",
                "scope",
                name="uq_skill_credential_binding",
            ),
        )


def downgrade() -> None:
    if _has_table("skill_credential_bindings"):
        op.drop_table("skill_credential_bindings")
