"""M22: add ``agents.model_fallback_list`` for ordered LLM fallback chains.

Revision ID: m22_add_agent_model_fallback
Revises: m21_add_daily_spend_aggregates
Create Date: 2026-04-29

A nullable JSON column that holds an ordered list of model UUIDs (stored as
strings to keep the JSON portable across PostgreSQL/SQLite). The runtime —
:func:`app.agent_runtime.model_factory.create_chat_model_with_fallback` —
walks the list when the primary ``model_id`` fails with a recoverable error
(401 / 404 / 429 / timeout / 5xx). Empty list and ``NULL`` both mean
"no fallback".

Reversible: ``downgrade`` drops the column.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m22_add_agent_model_fallback"
down_revision = "m21_add_daily_spend_aggregates"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return any(col["name"] == column for col in inspector.get_columns(table))


def upgrade() -> None:
    if not _has_column("agents", "model_fallback_list"):
        op.add_column(
            "agents",
            sa.Column("model_fallback_list", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    if _has_column("agents", "model_fallback_list"):
        op.drop_column("agents", "model_fallback_list")
