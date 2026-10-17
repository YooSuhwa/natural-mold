"""Checkpoint metadata cross-check (plan 8.8 "체크포인터", I-06).

LangGraph's checkpointer is a separate psycopg pool outside Moldy RLS. Every
run records its company in the checkpoint metadata (``moldy_tenant_id``, copied
from the run's ``configurable`` by LangGraph), and this job compares it with
the company of the conversation that owns the thread (``thread_id`` =
conversation id). A mismatch means a thread was written under another company.

``python -m app.tenancy.checkpoint_audit [--json]`` exits 1 on any mismatch.
Rows written before I-06 carry no company and are counted as unattributed;
threads with no conversation row (builder/assistant threads) as orphaned.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import UUID

import anyio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import platform_session

logger = logging.getLogger(__name__)

CHECKPOINT_TENANT_KEY = "moldy_tenant_id"
_SAMPLE = 20
_EXISTS = text("SELECT to_regclass('checkpoints') IS NOT NULL")
_PAGE = text(
    "WITH page AS (SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id > :after "
    "ORDER BY thread_id LIMIT :limit) "
    "SELECT page.thread_id, ck.metadata ->> 'moldy_tenant_id' AS recorded, "
    "conv.tenant_id::text AS expected, count(*) AS checkpoints "
    "FROM page JOIN checkpoints AS ck ON ck.thread_id = page.thread_id "
    "LEFT JOIN conversations AS conv ON conv.id = CASE WHEN page.thread_id ~ "
    "'^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$' THEN page.thread_id::uuid END "
    "GROUP BY page.thread_id, recorded, conv.tenant_id ORDER BY page.thread_id"
)


def checkpoint_tenant(tenant_id: UUID) -> dict[str, str]:
    """The ``configurable`` entry that LangGraph copies into checkpoint metadata."""
    return {CHECKPOINT_TENANT_KEY: str(tenant_id)}


def run_configurable(thread_id: str, run_id: UUID, tenant_id: UUID) -> dict[str, Any]:
    """Prepared-run ``configurable``: thread, run lease id and the run's company."""
    return {"thread_id": thread_id, "moldy_run_id": str(run_id), **checkpoint_tenant(tenant_id)}


@dataclass(frozen=True, slots=True)
class ThreadTenant:
    thread_id: str
    recorded: str | None
    expected: str | None
    checkpoints: int


@dataclass(slots=True)
class CheckpointTenantReport:
    threads: int = 0
    checkpoints: int = 0
    unattributed: int = 0
    orphaned: int = 0
    mismatched: int = 0
    mismatched_threads: list[str] = field(default_factory=list)

    def add(self, rows: Iterable[ThreadTenant]) -> None:
        seen: set[str] = set()
        bad: set[str] = set()
        for row in rows:
            seen.add(row.thread_id)
            self.checkpoints += row.checkpoints
            if row.expected is None:
                self.orphaned += row.checkpoints
            elif row.recorded is None:
                self.unattributed += row.checkpoints
            elif row.recorded != row.expected:
                self.mismatched += row.checkpoints
                bad.add(row.thread_id)
        self.threads += len(seen)
        room = _SAMPLE - len(self.mismatched_threads)
        self.mismatched_threads.extend(sorted(bad)[: max(room, 0)])


async def audit_checkpoint_tenants(db: AsyncSession, *, page: int = 500) -> CheckpointTenantReport:
    report = CheckpointTenantReport()
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return report
    if not (await db.execute(_EXISTS)).scalar_one():
        return report
    after = ""
    while True:
        result = await db.execute(_PAGE, {"after": after, "limit": page})
        rows = [ThreadTenant(r[0], r[1], r[2], int(r[3])) for r in result.all()]
        if not rows:
            break
        report.add(rows)
        after = rows[-1].thread_id
        await db.commit()  # short transactions; the platform scope is re-applied
    if report.mismatched:
        logger.error(
            "checkpoint_tenant_mismatch threads=%s checkpoints=%s sample=%s",
            len(report.mismatched_threads),
            report.mismatched,
            ",".join(report.mismatched_threads),
        )
    return report


async def _run(as_json: bool) -> int:
    async with platform_session("checkpoint.tenant_audit") as db:
        report = await audit_checkpoint_tenants(db)
    print(json.dumps(asdict(report)) if as_json else report)  # noqa: T201 - operator CLI
    return 1 if report.mismatched else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cross-check checkpoint company metadata.")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return anyio.run(_run, parser.parse_args(argv).as_json)


if __name__ == "__main__":
    raise SystemExit(main())
