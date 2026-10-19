"""F-09 (plan 11.8): shared operating memo proposals and the per-agent memo switch.

* ``memory_proposals.scope`` admits ``agent_memo`` (a proposal to an agent's shared
  ``AGENTS.md``, decided by its editors); ``ck_memory_proposals_scope`` is widened.
* ``agent_memory_settings.shared_memo_enabled`` (default off for new agents). Agents
  whose ``<data_root>/agents/<id>/AGENTS.md`` already has content start on: a settings
  row is inserted or updated for each of them.

Downgrade drops the column and narrows the check again (``agent_memo`` proposals are
deleted first, they cannot exist under the old schema).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "f09_agent_memo"
down_revision = "f11_credential_definition_hosts"
branch_labels = None
depends_on = None

_CHECK = "ck_memory_proposals_scope"
_WIDE = "scope in ('user', 'agent', 'agent_memo')"
_NARROW = "scope in ('user', 'agent')"


def data_dir() -> Path:
    from app.config import settings

    root = Path(settings.data_root)
    return root if root.is_absolute() else Path(__file__).resolve().parents[2] / root


def _agents_with_memo(root: Path) -> set[str]:
    found: set[str] = set()  # canonical UUID text
    agents = root / "agents"
    if not agents.is_dir():
        return found
    for memo in agents.glob("*/AGENTS.md"):
        try:
            agent_id = str(uuid.UUID(memo.parent.name))
            if memo.is_file() and memo.read_text(encoding="utf-8", errors="replace").strip():
                found.add(agent_id)
        except (ValueError, OSError):
            continue
    return found


def _backfill(bind: sa.Connection) -> None:
    candidates = _agents_with_memo(data_dir())
    if not candidates:
        return
    agents = sa.table("agents", sa.column("id", sa.Uuid()))
    settings = sa.table(
        "agent_memory_settings",
        sa.column("agent_id", sa.Uuid()),
        sa.column("shared_memo_enabled", sa.Boolean()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    wanted = [uuid.UUID(value) for value in sorted(candidates)]
    existing = set(bind.execute(sa.select(agents.c.id).where(agents.c.id.in_(wanted))).scalars())
    with_rows = {row[0] for row in bind.execute(sa.select(settings.c.agent_id))}
    now = datetime.now(UTC).replace(tzinfo=None)
    for agent_id in sorted(existing):
        if agent_id in with_rows:
            bind.execute(
                settings.update()
                .where(settings.c.agent_id == agent_id)
                .values(shared_memo_enabled=True)
            )
        else:
            bind.execute(
                settings.insert().values(
                    agent_id=agent_id, shared_memo_enabled=True, created_at=now, updated_at=now
                )
            )


def upgrade() -> None:
    with op.batch_alter_table("memory_proposals") as batch:
        batch.drop_constraint(_CHECK, type_="check")
        batch.create_check_constraint(_CHECK, _WIDE)
    with op.batch_alter_table("agent_memory_settings") as batch:
        batch.add_column(
            sa.Column(
                "shared_memo_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
    _backfill(op.get_bind())


def downgrade() -> None:
    op.execute("DELETE FROM memory_proposals WHERE scope = 'agent_memo'")
    with op.batch_alter_table("agent_memory_settings") as batch:
        batch.drop_column("shared_memo_enabled")
    with op.batch_alter_table("memory_proposals") as batch:
        batch.drop_constraint(_CHECK, type_="check")
        batch.create_check_constraint(_CHECK, _NARROW)
