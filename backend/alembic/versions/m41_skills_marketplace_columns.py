"""M41: Add marketplace lineage columns to ``skills`` (ADR-017 Slice A).

Revision ID: m41_skills_marketplace_columns
Revises: m40_marketplace_tables
Create Date: 2026-05-18

Adds 12 new columns to ``skills`` (Spec §3.7) so each row carries its
origin + publication lineage. Backfill (§15.2):

* ``is_system``                       — defaults ``false`` for all rows.
* ``source_kind``                     — NULL initially. Backfill: existing
  text-kind ⇒ ``'user'``; package-kind ⇒ ``'import'`` so we don't lie
  about provenance (they were uploaded by the owner).
* ``origin_kind`` (NOT NULL)          — NEW DEFAULT ``'created_by_me'``;
  backfill package skills to ``'imported_by_me'`` per Bezos OI / progress.txt.
* All other columns nullable / FK SET NULL.

Reversible. The backfill cannot recover the prior NULL state of
``source_kind``/``origin_kind`` on downgrade, but column drops still
restore the schema exactly.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m41_skills_marketplace_columns"
down_revision = "m40_marketplace_tables"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return any(col["name"] == column for col in inspector.get_columns(table))


def _has_constraint(table: str, name: str, kind: str = "foreignkey") -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    if kind == "foreignkey":
        return any(fk.get("name") == name for fk in inspector.get_foreign_keys(table))
    return False


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _col(name: str, type_: sa.types.TypeEngine, **kwargs) -> sa.Column:
    return sa.Column(name, type_, **kwargs)


NEW_COLUMNS: list[tuple[str, sa.Column]] = [
    ("is_system", _col("is_system", sa.Boolean(), nullable=False, server_default=sa.false())),
    ("source_kind", _col("source_kind", sa.String(40), nullable=True)),
    ("source_marketplace_item_id", _col("source_marketplace_item_id", sa.Uuid(), nullable=True)),
    (
        "source_marketplace_version_id",
        _col("source_marketplace_version_id", sa.Uuid(), nullable=True),
    ),
    ("source_commit", _col("source_commit", sa.String(80), nullable=True)),
    ("credential_requirements", _col("credential_requirements", sa.JSON(), nullable=True)),
    ("execution_profile", _col("execution_profile", sa.JSON(), nullable=True)),
    (
        "origin_kind",
        _col(
            "origin_kind",
            sa.String(40),
            nullable=False,
            server_default="created_by_me",
        ),
    ),
    ("origin_user_id", _col("origin_user_id", sa.Uuid(), nullable=True)),
    (
        "origin_marketplace_item_id",
        _col("origin_marketplace_item_id", sa.Uuid(), nullable=True),
    ),
    (
        "origin_marketplace_version_id",
        _col("origin_marketplace_version_id", sa.Uuid(), nullable=True),
    ),
    ("is_dirty", _col("is_dirty", sa.Boolean(), nullable=False, server_default=sa.false())),
]


FK_DEFS = [
    (
        "fk_skills_source_marketplace_item",
        "source_marketplace_item_id",
        "marketplace_items",
        "id",
        "SET NULL",
    ),
    (
        "fk_skills_source_marketplace_version",
        "source_marketplace_version_id",
        "marketplace_versions",
        "id",
        "SET NULL",
    ),
    (
        "fk_skills_origin_user",
        "origin_user_id",
        "users",
        "id",
        "SET NULL",
    ),
    (
        "fk_skills_origin_marketplace_item",
        "origin_marketplace_item_id",
        "marketplace_items",
        "id",
        "SET NULL",
    ),
    (
        "fk_skills_origin_marketplace_version",
        "origin_marketplace_version_id",
        "marketplace_versions",
        "id",
        "SET NULL",
    ),
]


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    for col_name, col_def in NEW_COLUMNS:
        if not _has_column("skills", col_name):
            op.add_column("skills", col_def)

    # FK constraints — Postgres only. SQLite tests don't depend on these
    # (Base.metadata.create_all rebuilds the schema directly from the ORM).
    if _is_postgres():
        for fk_name, local_col, ref_table, ref_col, on_delete in FK_DEFS:
            if not _has_constraint("skills", fk_name, kind="foreignkey"):
                op.create_foreign_key(
                    fk_name,
                    "skills",
                    ref_table,
                    [local_col],
                    [ref_col],
                    ondelete=on_delete,
                )

    # Backfill (§15.2):
    #   - source_kind: existing text skills → 'user', package skills → 'import'
    #   - origin_kind: package skills → 'imported_by_me' (default already
    #     'created_by_me', so only flip package rows)
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE skills SET source_kind = 'user' "
            "WHERE source_kind IS NULL AND kind = 'text'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE skills SET source_kind = 'import' "
            "WHERE source_kind IS NULL AND kind = 'package'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE skills SET origin_kind = 'imported_by_me' "
            "WHERE kind = 'package'"
        )
    )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    if _is_postgres():
        for fk_name, *_ in FK_DEFS:
            if _has_constraint("skills", fk_name, kind="foreignkey"):
                op.drop_constraint(fk_name, "skills", type_="foreignkey")

    for col_name, _col_def in reversed(NEW_COLUMNS):
        if _has_column("skills", col_name):
            op.drop_column("skills", col_name)
