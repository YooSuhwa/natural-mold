"""M38: refresh_tokens.expires_at single-column index for GC.

Revision ID: m38_refresh_token_expires_idx
Revises: m37_refresh_token_replaced_by
Create Date: 2026-05-18

The nightly GC job (``app.services.refresh_token_gc``) issues
``DELETE FROM refresh_tokens WHERE expires_at < cutoff``. The m36
partial index ``ix_refresh_tokens_active`` is leading on ``user_id``
and filtered to ``revoked_at IS NULL`` — useless for this query, which
scans across all users and *most* targets are revoked rows.

A single-column index on ``expires_at`` keeps the GC query in index-scan
territory regardless of table size. ``IF NOT EXISTS`` guard handles the
idempotent re-run case.

Operators with large prod tables can opt-out of the locking ``CREATE
INDEX`` and run ``CREATE INDEX CONCURRENTLY`` manually before running
``alembic upgrade``; the migration's ``IF NOT EXISTS`` will then no-op.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m38_refresh_token_expires_idx"
down_revision = "m37_refresh_token_replaced_by"
branch_labels = None
depends_on = None


INDEX_NAME = "ix_refresh_tokens_expires_at"


def _has_index(table: str, name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return any(ix["name"] == name for ix in inspector.get_indexes(table))


def upgrade() -> None:
    if not _has_index("refresh_tokens", INDEX_NAME):
        op.create_index(
            INDEX_NAME,
            "refresh_tokens",
            ["expires_at"],
        )


def downgrade() -> None:
    if _has_index("refresh_tokens", INDEX_NAME):
        op.drop_index(INDEX_NAME, table_name="refresh_tokens")
