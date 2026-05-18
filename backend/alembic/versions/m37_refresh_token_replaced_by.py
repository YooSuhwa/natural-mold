"""M37: refresh_tokens.replaced_by_id (race-vs-replay disambiguation).

Revision ID: m37_refresh_token_replaced_by
Revises: m36_multiuser_auth
Create Date: 2026-05-18

Adds a self-referential ``replaced_by_id`` column so the refresh-token
rotation flow can tell a tab-race apart from a stolen-token replay:

* On a normal rotation, the old row's ``replaced_by_id`` is set to the
  newly minted row.
* When an already-revoked refresh token is re-presented, the row is a
  *race* if (a) ``replaced_by_id`` is set, (b) the replacement is still
  active, (c) the revocation is within ``settings.refresh_rotation_grace_seconds``,
  and (d) the originating user-agent matches. Otherwise it is a replay
  and the user's whitelist is burned.

Idempotent. ``ON DELETE SET NULL`` so revoking the replacement (e.g.
during a real mass-revoke) leaves the historical row intact.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m37_refresh_token_replaced_by"
down_revision = "m36_multiuser_auth"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return any(col["name"] == column for col in inspector.get_columns(table))


def _has_constraint(table: str, name: str, kind: str = "check") -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    if kind == "foreignkey":
        return any(fk.get("name") == name for fk in inspector.get_foreign_keys(table))
    if kind == "check":
        try:
            return any(
                c.get("name") == name for c in inspector.get_check_constraints(table)
            )
        except NotImplementedError:
            return False
    return False


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


FK_NAME = "fk_refresh_tokens_replaced_by_id"


def upgrade() -> None:
    if not _has_column("refresh_tokens", "replaced_by_id"):
        # SQLite (tests) can't ALTER TABLE ADD COLUMN with an inline FK,
        # so we add the column first and attach the FK on Postgres only.
        # SQLite is fine without the named FK — the application enforces
        # the invariant.
        op.add_column(
            "refresh_tokens",
            sa.Column("replaced_by_id", sa.Uuid(), nullable=True),
        )

    if _is_postgres() and not _has_constraint(
        "refresh_tokens", FK_NAME, kind="foreignkey"
    ):
        op.create_foreign_key(
            FK_NAME,
            "refresh_tokens",
            "refresh_tokens",
            ["replaced_by_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    if _is_postgres() and _has_constraint(
        "refresh_tokens", FK_NAME, kind="foreignkey"
    ):
        op.drop_constraint(FK_NAME, "refresh_tokens", type_="foreignkey")
    if _has_column("refresh_tokens", "replaced_by_id"):
        op.drop_column("refresh_tokens", "replaced_by_id")
