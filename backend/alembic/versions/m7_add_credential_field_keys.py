"""M7: add credentials.field_keys cache column + backfill

Revision ID: m7_add_credential_field_keys
Revises: m6_add_credentials
Create Date: 2026-04-17
"""

from __future__ import annotations

import json
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

    from app.config import settings

    if not settings.encryption_key:
        logger.warning(
            "ENCRYPTION_KEY not set — skipping field_keys backfill; "
            "legacy rows will be filled lazily via fallback path"
        )
        return

    from app.services.encryption import decrypt_api_key

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, data_encrypted FROM credentials")).fetchall()

    for row in rows:
        cred_id = row[0]
        ciphertext = row[1]
        try:
            plaintext = decrypt_api_key(ciphertext)
            payload = json.loads(plaintext)
            keys = list(payload.keys()) if isinstance(payload, dict) else []
        except Exception as exc:  # noqa: BLE001 — tolerant backfill
            logger.warning("Failed to backfill field_keys for credential %s: %s", cred_id, exc)
            keys = []

        bind.execute(
            sa.text("UPDATE credentials SET field_keys = :keys WHERE id = :id"),
            {"keys": json.dumps(keys), "id": cred_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("credentials") as batch_op:
        batch_op.drop_column("field_keys")
