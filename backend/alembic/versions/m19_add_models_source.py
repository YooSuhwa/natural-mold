"""M19: extend ``models`` with discovery-friendly metadata columns.

Revision ID: m19_add_models_source
Revises: m18_greenfield_credentials
Create Date: 2026-04-29

Adds the columns the M7 model-discovery hybrid needs to round-trip a
``DiscoveredModel`` (max output tokens, capability flags, pricing source).
All new columns are nullable so existing rows survive untouched.

Downgrade drops the columns; the grandparent schema is restored.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m19_add_models_source"
down_revision = "m18_greenfield_credentials"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    # All additions are nullable / no default → safe on populated tables.
    if not _has_column("models", "max_output_tokens"):
        op.add_column("models", sa.Column("max_output_tokens", sa.Integer(), nullable=True))
    if not _has_column("models", "supports_vision"):
        op.add_column("models", sa.Column("supports_vision", sa.Boolean(), nullable=True))
    if not _has_column("models", "supports_function_calling"):
        op.add_column(
            "models", sa.Column("supports_function_calling", sa.Boolean(), nullable=True)
        )
    if not _has_column("models", "supports_reasoning"):
        op.add_column("models", sa.Column("supports_reasoning", sa.Boolean(), nullable=True))
    if not _has_column("models", "source"):
        op.add_column("models", sa.Column("source", sa.String(20), nullable=True))


def downgrade() -> None:
    for column in (
        "source",
        "supports_reasoning",
        "supports_function_calling",
        "supports_vision",
        "max_output_tokens",
    ):
        if _has_column("models", column):
            op.drop_column("models", column)
