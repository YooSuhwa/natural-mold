"""Workflow run source (feature plan v2 순번 23, workflow-plan W0).

A workflow step runs with ``run_source = 'WORKFLOW'``. Agent MCP delegations carry
``allowed_run_source`` over every run source (F-01, 7.1 default), so the delegations that
hold exactly the previous full list gain ``WORKFLOW``; a list someone narrowed is left
alone. OpenFGA tuples follow through the reconcile repair (a changed condition is
rewritten). Downgrade takes ``WORKFLOW`` back out of the lists this revision widened.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from alembic import op

revision = "i26_workflow_run_source"
down_revision = "i25_memory_trigger_memo"
branch_labels = None
depends_on = None

BEFORE = ["CHAT", "TRIGGER", "CHANNEL", "SUBAGENT", "API"]
AFTER = [*BEFORE, "WORKFLOW"]  # tuple_types.RUN_SOURCES

_grants = sa.table(
    "resource_grants",
    sa.column("id", sa.Uuid()),
    sa.column("condition_name", sa.String()),
    sa.column("condition_context", sa.JSON()),
)


def _sources(context: Any) -> list[str] | None:
    if isinstance(context, str):  # SQLite returns JSON text
        context = json.loads(context)
    raw = context.get("allowed_sources") if isinstance(context, dict) else None
    return list(raw) if isinstance(raw, list) else None


def _rewrite(old: list[str], new: list[str]) -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(_grants.c.id, _grants.c.condition_context).where(
            _grants.c.condition_name == "allowed_run_source"
        )
    ).all()
    for row in rows:
        if _sources(row.condition_context) == old:
            bind.execute(
                sa.update(_grants)
                .where(_grants.c.id == row.id)
                .values(condition_context={"allowed_sources": new})
            )


def upgrade() -> None:
    _rewrite(BEFORE, AFTER)


def downgrade() -> None:
    _rewrite(AFTER, BEFORE)
