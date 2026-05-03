"""M7: add credentials.field_keys cache column + backfill

Revision ID: m7_add_credential_field_keys
Revises: m6_add_credentials
Create Date: 2026-04-17
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from alembic import op

revision = "m7_add_credential_field_keys"
down_revision = "m6_add_credentials"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    op.add_column(
        "credentials",
        sa.Column("field_keys", sa.JSON(), nullable=True),
    )

    # Legacy ``app.services.encryption`` was removed in M5 along with the in-DB
    # Fernet keys. Fresh DB roll-ups iterate zero credential rows here, and any
    # production DB has already executed this migration (head is past m7).
    # Keeping the backfill loop as a self-contained inline Fernet would only be
    # exercised by a hypothetical DB stuck pre-m7 with legacy ciphertext — at
    # which point the lazy fallback path in ``credential_resolution`` recovers
    # the keys on first use anyway.
    bind = op.get_bind()
    row_count = bind.execute(sa.text("SELECT COUNT(*) FROM credentials")).scalar() or 0
    if row_count:
        logger.warning(
            "m7: %s credential rows present pre-backfill — field_keys will be "
            "populated lazily on first decrypt via credential_resolution",
            row_count,
        )


def downgrade() -> None:
    with op.batch_alter_table("credentials") as batch_op:
        batch_op.drop_column("field_keys")
