"""M24: add ``credentials.is_system`` to separate operator keys from user keys.

Revision ID: m24_add_credential_is_system
Revises: m23_add_model_default_cred
Create Date: 2026-04-30

System keys (Fix Agent / builder / image generation / future bootstrap
flows) are intentionally distinct from user agent keys: cost is on the
operator, lifecycle is operator-managed, and users must not see or
override them.

A simple boolean flag on the existing ``credentials`` table is enough —
no need for a parallel table. ``user_id`` stays NOT NULL so the row still
has an owner (the operator who registered it). ``is_system=True`` rows
are filtered OUT of regular ``list_for_user`` calls so user-facing
pickers (model Health panel, agent settings, MCP wizard) never surface
them.

Reversible.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m24_add_credential_is_system"
down_revision = "m23_add_model_default_cred"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return any(col["name"] == column for col in inspector.get_columns(table))


def upgrade() -> None:
    if _has_column("credentials", "is_system"):
        return
    op.add_column(
        "credentials",
        sa.Column(
            "is_system",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    # ``server_default`` keeps existing rows as ``False``; subsequent INSERTs
    # may explicitly opt in.


def downgrade() -> None:
    if _has_column("credentials", "is_system"):
        with op.batch_alter_table("credentials") as batch:
            batch.drop_column("is_system")
