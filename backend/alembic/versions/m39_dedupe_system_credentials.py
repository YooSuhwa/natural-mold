"""M39: collapse duplicate system credentials by definition_key.

Revision ID: m39_dedupe_system_creds
Revises: m38_refresh_token_expires_idx
Create Date: 2026-05-18

Before this PR system credentials could accumulate when the same provider
was registered through both ``bootstrap_from_env`` (``[system] X``) and a
manual UI insertion (``X``). The seed is idempotent on its own marker
name but doesn't deduplicate against pre-existing rows.

For each ``(definition_key, is_system=True)`` group with more than one
row this migration:
1. Picks the keeper — the most recently created row (``ORDER BY
   created_at DESC, id DESC`` for tie-break determinism).
2. Re-points ``agents.llm_credential_id`` and
   ``models.default_credential_id`` from duplicates to the keeper.
   (``credential_defaults`` and ``credential_audit_logs`` cascade.)
3. Deletes the duplicate rows.

Idempotent: a re-run on a clean DB finds zero groups and exits without
side-effects. SQLite test path also runs cleanly — operates entirely on
common SQL.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m39_dedupe_system_creds"
down_revision = "m38_refresh_token_expires_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # Find duplicate groups + their keeper. ``MAX(created_at)`` ties broken
    # by ``MAX(id::text)`` so the result is reproducible across reruns.
    groups = bind.execute(
        sa.text(
            """
            SELECT definition_key, COUNT(*) AS n
            FROM credentials
            WHERE is_system = true
            GROUP BY definition_key
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()

    for definition_key, _count in groups:
        rows = bind.execute(
            sa.text(
                """
                SELECT id, created_at
                FROM credentials
                WHERE is_system = true AND definition_key = :k
                ORDER BY created_at DESC, CAST(id AS VARCHAR) DESC
                """
            ),
            {"k": definition_key},
        ).fetchall()
        keeper_id = rows[0][0]
        loser_ids = [r[0] for r in rows[1:]]

        # Re-point references that would otherwise be nulled by the
        # ON DELETE SET NULL cascade. ``IN :losers`` with
        # ``expanding=True`` works portably across asyncpg + aiosqlite,
        # which is why we don't use ``= ANY(...)`` here.
        bind.execute(
            sa.text(
                "UPDATE agents SET llm_credential_id = :keeper "
                "WHERE llm_credential_id IN :losers"
            ).bindparams(sa.bindparam("losers", expanding=True)),
            {"keeper": keeper_id, "losers": loser_ids},
        )
        bind.execute(
            sa.text(
                "UPDATE models SET default_credential_id = :keeper "
                "WHERE default_credential_id IN :losers"
            ).bindparams(sa.bindparam("losers", expanding=True)),
            {"keeper": keeper_id, "losers": loser_ids},
        )

        bind.execute(
            sa.text(
                "DELETE FROM credentials WHERE id IN :losers"
            ).bindparams(sa.bindparam("losers", expanding=True)),
            {"losers": loser_ids},
        )


def downgrade() -> None:
    # Data migration — duplicates cannot be reconstructed. Intentional no-op.
    pass
