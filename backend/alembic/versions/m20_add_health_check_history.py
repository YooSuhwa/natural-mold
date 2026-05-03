"""M20: add ``health_check_history`` time-series table.

Revision ID: m20_add_health_check_history
Revises: m19_add_models_source
Create Date: 2026-04-29

Stores per-target health probe results so the UI can show "latest status" +
"last N checks" for every registered model and MCP server. The
``(target_kind, target_id, checked_at DESC)`` index keeps both queries cheap.

This migration is fully reversible — ``downgrade`` drops the table and its
index. dialect-aware UUID/timestamp helpers mirror m18 / m19.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m20_add_health_check_history"
down_revision = "m19_add_models_source"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Helpers — dialect-aware UUID column type and timestamp default.
# ---------------------------------------------------------------------------


def _uuid_col() -> sa.types.TypeEngine:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects import postgresql

        return postgresql.UUID(as_uuid=True)
    return sa.String(36)


def _utc_now_default() -> sa.TextClause:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return sa.text("now()")
    return sa.text("CURRENT_TIMESTAMP")


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return name in inspector.get_table_names()


def _has_index(table: str, name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return any(ix["name"] == name for ix in inspector.get_indexes(table))


# ---------------------------------------------------------------------------
# upgrade / downgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    uuid_type = _uuid_col()
    now_default = _utc_now_default()

    if not _has_table("health_check_history"):
        op.create_table(
            "health_check_history",
            sa.Column("id", uuid_type, primary_key=True, nullable=False),
            sa.Column("target_kind", sa.String(20), nullable=False),
            sa.Column("target_id", uuid_type, nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("error_kind", sa.String(40), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("raw_result", sa.JSON(), nullable=True),
            sa.Column(
                "checked_at",
                sa.DateTime(),
                nullable=False,
                server_default=now_default,
            ),
        )

    if not _has_index(
        "health_check_history", "ix_health_check_history_target_checked_at"
    ):
        op.create_index(
            "ix_health_check_history_target_checked_at",
            "health_check_history",
            ["target_kind", "target_id", "checked_at"],
        )


def downgrade() -> None:
    if _has_index(
        "health_check_history", "ix_health_check_history_target_checked_at"
    ):
        op.drop_index(
            "ix_health_check_history_target_checked_at",
            table_name="health_check_history",
        )
    if _has_table("health_check_history"):
        op.drop_table("health_check_history")
