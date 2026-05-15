"""M36: Multi-user auth (ADR-016).

Revision ID: m36_multiuser_auth
Revises: m35_builder_session_fk_setnull
Create Date: 2026-05-09

Implements ADR-016 §4 — adds the columns and tables required to graduate
natural-mold from a single mock user to a real multi-user system:

1. ``users`` gains 12 new columns (auth state + Phase-2 reservations).
2. ``refresh_tokens`` table created with rotation/replay-detection indexes.
3. ``tools.is_system`` column + CHECK constraint
   (``is_system=true`` ⇒ ``user_id IS NULL``).
4. ``credentials.user_id`` relaxed to nullable + matching CHECK constraint
   (was NOT NULL since m18). System credentials must detach from any
   specific user — invariant enforced at the DB layer.
5. ``agents`` / ``builder_sessions`` / ``agent_triggers`` ``user_id`` FK
   gain ``ON DELETE CASCADE`` so deleting a user deterministically cleans
   up everything they own.
6. Data backfill:
   - Mock user (``settings.mock_user_id``) → ``is_super_user=true``
     (only if the row exists; idempotent).
   - Pre-existing ``tools.user_id IS NULL`` rows → ``is_system=true``
     (these were already operator-managed by convention).
   - Pre-existing ``credentials`` already had ``is_system`` (m24), so no
     extra backfill needed there beyond the CHECK constraint.

Reversible. The downgrade preserves data — it cannot recover the
``credentials.user_id`` NOT NULL state if any row has been NULLed (CHECK
will fail). Operators rolling back must re-assign system credentials to a
specific user first.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m36_multiuser_auth"
down_revision = "m35_builder_session_fk_setnull"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    if kind == "check":
        try:
            return any(c.get("name") == name for c in inspector.get_check_constraints(table))
        except NotImplementedError:
            return False
    if kind == "foreignkey":
        return any(c.get("name") == name for c in inspector.get_foreign_keys(table))
    return False


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


_USER_COLUMNS: list[tuple[str, sa.Column]] = [
    ("hashed_password", sa.Column("hashed_password", sa.String(length=255), nullable=True)),
    (
        "is_active",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    ),
    (
        "is_super_user",
        sa.Column(
            "is_super_user",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    ),
    (
        "last_login_at",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    ),
    ("last_login_ip", sa.Column("last_login_ip", sa.String(length=45), nullable=True)),
    (
        "failed_login_attempts",
        sa.Column(
            "failed_login_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    ),
    (
        "locked_until",
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    ),
    (
        "email_verified_at",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    ),
    (
        "email_verify_token",
        sa.Column("email_verify_token", sa.String(length=64), nullable=True),
    ),
    (
        "email_verify_expires_at",
        sa.Column("email_verify_expires_at", sa.DateTime(timezone=True), nullable=True),
    ),
    (
        "password_reset_token",
        sa.Column("password_reset_token", sa.String(length=64), nullable=True),
    ),
    (
        "password_reset_expires_at",
        sa.Column(
            "password_reset_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
    ),
]


def upgrade() -> None:
    bind = op.get_bind()

    # 1. users: add auth columns ------------------------------------------------
    for name, column in _USER_COLUMNS:
        if not _has_column("users", name):
            op.add_column("users", column)

    # 2. refresh_tokens table ---------------------------------------------------
    inspector = sa.inspect(bind)
    if "refresh_tokens" not in inspector.get_table_names():
        op.create_table(
            "refresh_tokens",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column(
                "issued_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("user_agent", sa.Text(), nullable=True),
            sa.Column("ip", sa.String(length=45), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.create_index(
            "ix_refresh_tokens_token_hash",
            "refresh_tokens",
            ["token_hash"],
            unique=True,
        )
        op.create_index(
            "ix_refresh_tokens_user_id",
            "refresh_tokens",
            ["user_id"],
        )
        # Active-token sweep — ``WHERE revoked_at IS NULL`` partial index
        # keeps the lookup cheap when most rows are revoked. Postgres-only;
        # SQLite (tests) silently uses the regular index above.
        if _is_postgres():
            op.execute(
                sa.text(
                    "CREATE INDEX IF NOT EXISTS ix_refresh_tokens_active "
                    "ON refresh_tokens (user_id, expires_at) "
                    "WHERE revoked_at IS NULL"
                )
            )

    # 3. tools.is_system + CHECK constraint -------------------------------------
    if not _has_column("tools", "is_system"):
        op.add_column(
            "tools",
            sa.Column(
                "is_system",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
    # Backfill — pre-existing rows with NULL user_id are operator-managed
    # tools (seeded prebuilt definitions). Promote them to is_system=true so
    # the new CHECK constraint and route filters agree.
    op.execute(
        sa.text(
            "UPDATE tools SET is_system = true "
            "WHERE user_id IS NULL AND is_system = false"
        )
    )
    if _is_postgres() and not _has_constraint("tools", "ck_tools_system_user_null"):
        op.create_check_constraint(
            "ck_tools_system_user_null",
            "tools",
            "(is_system = false) OR (user_id IS NULL)",
        )

    # 4. credentials.user_id NOT NULL → nullable + CHECK constraint -------------
    # ``alter_column`` with batch_alter_table for SQLite compat. PG accepts
    # the direct form; we use batch unconditionally for safety.
    with op.batch_alter_table("credentials") as batch:
        batch.alter_column("user_id", existing_type=sa.Uuid(), nullable=True)
    # Backfill — m24 introduced ``is_system`` but did not detach those rows
    # from the operator account that registered them. ADR-016 §4.4 says
    # ``is_system=true`` MUST imply ``user_id IS NULL``; the CHECK below
    # would otherwise fail to apply on legacy databases.
    op.execute(
        sa.text("UPDATE credentials SET user_id = NULL WHERE is_system = true")
    )
    if _is_postgres() and not _has_constraint(
        "credentials", "ck_credentials_system_user_null"
    ):
        op.create_check_constraint(
            "ck_credentials_system_user_null",
            "credentials",
            "(is_system = false) OR (user_id IS NULL)",
        )

    # 5. ON DELETE CASCADE for user_id FKs --------------------------------------
    # Postgres auto-names FKs as ``{table}_{column}_fkey``. We drop+recreate.
    if _is_postgres():
        for table in ("agents", "builder_sessions", "agent_triggers"):
            constraint = f"{table}_user_id_fkey"
            if _has_constraint(table, constraint, kind="foreignkey"):
                op.drop_constraint(constraint, table, type_="foreignkey")
            op.create_foreign_key(
                constraint,
                table,
                "users",
                ["user_id"],
                ["id"],
                ondelete="CASCADE",
            )

    # 6. Data backfill — mock user → super_user ---------------------------------
    # Idempotent: only updates when a user with the canonical mock UUID
    # exists. Production DBs without that row are unaffected.
    op.execute(
        sa.text(
            "UPDATE users SET is_super_user = true "
            "WHERE id = '00000000-0000-0000-0000-000000000001' "
            "AND is_super_user = false"
        )
    )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # 5. Restore loose FK (no ondelete) -----------------------------------------
    if _is_postgres():
        for table in ("agent_triggers", "builder_sessions", "agents"):
            constraint = f"{table}_user_id_fkey"
            if _has_constraint(table, constraint, kind="foreignkey"):
                op.drop_constraint(constraint, table, type_="foreignkey")
            op.create_foreign_key(
                constraint,
                table,
                "users",
                ["user_id"],
                ["id"],
            )

    # 4. credentials: drop CHECK + restore NOT NULL -----------------------------
    if _is_postgres() and _has_constraint(
        "credentials", "ck_credentials_system_user_null"
    ):
        op.drop_constraint("ck_credentials_system_user_null", "credentials", type_="check")
    # Re-tightening to NOT NULL would fail if any system credential has
    # ``user_id IS NULL``. Leave as nullable on downgrade (safer than data
    # loss); operators must reseat ownership before re-applying m18 rules.

    # 3. tools: drop CHECK + drop is_system -------------------------------------
    if _is_postgres() and _has_constraint("tools", "ck_tools_system_user_null"):
        op.drop_constraint("ck_tools_system_user_null", "tools", type_="check")
    if _has_column("tools", "is_system"):
        with op.batch_alter_table("tools") as batch:
            batch.drop_column("is_system")

    # 2. refresh_tokens table ---------------------------------------------------
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "refresh_tokens" in inspector.get_table_names():
        if _is_postgres():
            op.execute(sa.text("DROP INDEX IF EXISTS ix_refresh_tokens_active"))
        op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
        op.drop_index("ix_refresh_tokens_token_hash", table_name="refresh_tokens")
        op.drop_table("refresh_tokens")

    # 1. users: drop auth columns ----------------------------------------------
    for name, _ in reversed(_USER_COLUMNS):
        if _has_column("users", name):
            with op.batch_alter_table("users") as batch:
                batch.drop_column(name)
