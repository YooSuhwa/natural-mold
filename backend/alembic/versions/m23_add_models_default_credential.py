"""M23: add ``models.default_credential_id`` for per-model default credential.

Revision ID: m23_add_model_default_cred
Revises: m22_add_agent_model_fallback
Create Date: 2026-04-30

A nullable UUID FK to ``credentials.id``. Captures the user's intent at
``Add model`` time — "this model is meant to be used with this credential" —
so the Health panel default and (later) any auto-binding flows respect that
choice instead of falling back to the first provider-matched credential.

``ON DELETE SET NULL`` so deleting a credential doesn't cascade-delete the
model row; the picker simply falls back to provider-matched options.

Reversible: ``downgrade`` drops the column.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m23_add_model_default_cred"
down_revision = "m22_add_agent_model_fallback"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return any(col["name"] == column for col in inspector.get_columns(table))


def upgrade() -> None:
    if _has_column("models", "default_credential_id"):
        return
    op.add_column(
        "models",
        sa.Column(
            "default_credential_id",
            sa.UUID(),
            sa.ForeignKey("credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    if _has_column("models", "default_credential_id"):
        with op.batch_alter_table("models") as batch:
            batch.drop_column("default_credential_id")
