"""M42: ``agent_skills.config`` JSON column (ADR-017 Slice A).

Revision ID: m42_agent_skills_config
Revises: m41_skills_marketplace_columns
Create Date: 2026-05-18

Adds the ``config`` column to ``agent_skills`` so agent-scoped overrides
(credential bindings, future parameter overrides) can be stored without
touching the shared ``Skill`` row.

Schema example::

    {"credential_bindings": {"srt_account": "<credential-uuid>"}}

Reversible. Defaults to NULL — existing links retain their behavior.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m42_agent_skills_config"
down_revision = "m41_skills_marketplace_columns"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return any(col["name"] == column for col in inspector.get_columns(table))


def upgrade() -> None:
    if not _has_column("agent_skills", "config"):
        op.add_column(
            "agent_skills",
            sa.Column("config", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    if _has_column("agent_skills", "config"):
        op.drop_column("agent_skills", "config")
