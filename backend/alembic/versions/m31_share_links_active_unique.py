"""M31: ``share_links`` partial unique index — at most one active per conversation.

Revision ID: m31_share_links_active_unique
Revises: m30_share_links
Create Date: 2026-05-01

Without this, two concurrent POSTs to ``/share`` both pass the existence
check in ``share_service.create_or_get_active_share`` and both INSERT,
producing duplicate active rows. The service now catches ``IntegrityError``
and re-fetches the winning row, so this index is the database-level guard
that makes the create-then-recover flow correct.

Reversible.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m31_share_links_active_unique"
down_revision = "m30_share_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_share_links_active_per_conversation",
        "share_links",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
        sqlite_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_share_links_active_per_conversation", table_name="share_links"
    )
