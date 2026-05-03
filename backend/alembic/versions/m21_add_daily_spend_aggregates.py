"""M21: add per-user / per-agent / per-model daily spend aggregate tables.

Revision ID: m21_add_daily_spend_aggregates
Revises: m20_add_health_check_history
Create Date: 2026-04-29

Three sibling tables, one per axis (user / agent / model). The
:class:`app.services.spend_writer.DailySpendUpdateQueue` UPSERTs into the
unique ``(date, target_id)`` key so concurrent agent invocations roll up into
a single row instead of generating one INSERT per request. The raw
``token_usages`` log is retained — these aggregates are an additive read
surface for the dashboard.

Reversible: ``downgrade`` drops all three tables and their indexes. Helpers
mirror m18 / m19 / m20 (dialect-aware UUID + ``now()``).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m21_add_daily_spend_aggregates"
down_revision = "m20_add_health_check_history"
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
# Table specs — same shape, three different FK targets.
# ---------------------------------------------------------------------------


_TABLES: tuple[tuple[str, str, str, str, str, str], ...] = (
    # (table_name, target_col, target_fk, unique_name, ix_date_name, ix_target_date_name)
    (
        "daily_spend_user",
        "user_id",
        "users.id",
        "uq_daily_spend_user_date_user",
        "ix_daily_spend_user_date",
        "ix_daily_spend_user_user_date",
    ),
    (
        "daily_spend_agent",
        "agent_id",
        "agents.id",
        "uq_daily_spend_agent_date_agent",
        "ix_daily_spend_agent_date",
        "ix_daily_spend_agent_agent_date",
    ),
    (
        "daily_spend_model",
        "model_id",
        "models.id",
        "uq_daily_spend_model_date_model",
        "ix_daily_spend_model_date",
        "ix_daily_spend_model_model_date",
    ),
)


# ---------------------------------------------------------------------------
# upgrade / downgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    uuid_type = _uuid_col()
    now_default = _utc_now_default()

    for (
        table_name,
        target_col,
        target_fk,
        unique_name,
        ix_date_name,
        ix_target_date_name,
    ) in _TABLES:
        if not _has_table(table_name):
            op.create_table(
                table_name,
                sa.Column("id", uuid_type, primary_key=True, nullable=False),
                sa.Column("date", sa.Date(), nullable=False),
                sa.Column(
                    target_col,
                    uuid_type,
                    sa.ForeignKey(target_fk, ondelete="CASCADE"),
                    nullable=False,
                ),
                sa.Column("total_tokens_in", sa.Integer(), nullable=False, server_default="0"),
                sa.Column("total_tokens_out", sa.Integer(), nullable=False, server_default="0"),
                sa.Column(
                    "total_cost_usd",
                    sa.Numeric(20, 8),
                    nullable=False,
                    server_default="0",
                ),
                sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
                sa.Column(
                    "updated_at",
                    sa.DateTime(),
                    nullable=False,
                    server_default=now_default,
                ),
                sa.UniqueConstraint("date", target_col, name=unique_name),
            )

        if not _has_index(table_name, ix_date_name):
            op.create_index(ix_date_name, table_name, ["date"])
        if not _has_index(table_name, ix_target_date_name):
            op.create_index(ix_target_date_name, table_name, [target_col, "date"])


def downgrade() -> None:
    # Reverse order so dependent indexes drop before tables.
    for (
        table_name,
        _target_col,
        _target_fk,
        _unique_name,
        ix_date_name,
        ix_target_date_name,
    ) in reversed(_TABLES):
        if _has_index(table_name, ix_target_date_name):
            op.drop_index(ix_target_date_name, table_name=table_name)
        if _has_index(table_name, ix_date_name):
            op.drop_index(ix_date_name, table_name=table_name)
        if _has_table(table_name):
            op.drop_table(table_name)
