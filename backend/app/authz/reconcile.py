"""Bootstrap fill, rebuild and reconcile of the OpenFGA store against Postgres (C-07, 8.6).

A run fixes its hold window first (``reconcile_holds``), reads the stored tuples once (no
DB transaction open), then handles one partition at a time - each company, then the
platform partition in a store-wide run - in short transactions:

1. snapshot: ``load_snapshot`` for the partition -> ``expected_tuples`` (active rows only);
2. diff against the stored tuples the partition claims (``StoredIndex``);
3. hold + recheck + enqueue: recently active keys and marker-blocked writes are held, the
   partition is re-read and writes no longer expected / deletes now expected are dropped,
   and the repairs are queued as ``authz_outbox`` rows (``source_table='authz_reconcile'``).

Every repair goes through the outbox - deletes and writes alike, in every mode - so the
outbox's per-key ``seq`` ordering applies it after any older pending row of its key.
``drain=True`` then applies the run's own rows (CLI); the daily job leaves them to the drain.

Modes: ``dry_run`` reports only (no writes, no rows); ``fill`` (bootstrap) queues missing
tuples only and never deletes; ``repair`` (rebuild/reconcile ``--apply``, daily job) queues
delete for extra, replace-write for changed and write for missing.

Sizing: the stored side is one dict entry per OpenFGA tuple plus a ref index (roughly
0.5 KB per tuple, ~500 MB per million tuples); the expected side is one company at a time.
Held tuples are reported but do not count as a difference (exit codes, ``diff``).
"""

import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from itertools import batched
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import reconcile_holds as holds
from app.authz import tuple_rows as rows
from app.authz.outbox import MAX_BATCH, TupleOp, enqueue, process_outbox, tuple_key
from app.authz.reconcile_diff import (
    Key,
    StoredIndex,
    TupleDiff,
    diff_tuples,
    hold,
    in_scope,
    key_of,
    recheck,
    scope_refs,
)
from app.authz.reconcile_holds import SOURCE_TABLE, Partition
from app.authz.tuple_snapshot import PLATFORM, load_snapshot
from app.authz.tuple_types import RelationTuple
from app.authz.tuples import expected_tuples
from app.database import platform_sessions

__all__ = ["SOURCE_TABLE", "ReconcileReport", "reconcile_sessions", "reconcile_store"]
logger = logging.getLogger(__name__)
Mode = Literal["dry_run", "fill", "repair"]
Sessions = Callable[[], AbstractAsyncContextManager[AsyncSession]]
SAMPLE = 20


class TupleStore(Protocol):
    def read_all(self, *, page_size: int = 100) -> AsyncIterator[RelationTuple]: ...

    async def write(self, tuples: Sequence[RelationTuple]) -> None: ...

    async def delete(self, tuples: Sequence[RelationTuple]) -> None: ...


class ReconcileReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    scope: str
    mode: Mode
    expected: int = 0
    stored: int = 0
    missing: int = 0
    extra: int = 0
    changed: int = 0
    held: int = 0
    queued: int = 0
    failed: int = 0
    outbox_ids: list[UUID] = []
    sample_missing: list[str] = []
    sample_extra: list[str] = []
    sample_changed: list[str] = []
    sample_held: list[str] = []

    @property
    def diff(self) -> int:
        return self.missing + self.extra + self.changed

    def add(self, diff: TupleDiff, expected: int, stored: int) -> "ReconcileReport":
        update: dict[str, object] = {"expected": self.expected + expected}
        update["stored"] = self.stored + stored
        for name in ("missing", "extra", "changed", "held"):
            items: tuple[RelationTuple, ...] = getattr(diff, name)
            update[name] = getattr(self, name) + len(items)
            sample = getattr(self, f"sample_{name}")
            update[f"sample_{name}"] = [*sample, *map(tuple_key, items)][:SAMPLE]
        return self.model_copy(update=update)


Projection = tuple[dict[Key, RelationTuple], frozenset[str] | None]


def _expected(snapshot: rows.Snapshot, part: Partition, now: datetime) -> Projection:
    refs = None
    if part.tenant_id is not None:
        refs = scope_refs(snapshot, tenant_id=part.tenant_id, org_id=part.org_id)
    want = {
        key_of(t): t
        for t in expected_tuples(snapshot, now=now)
        if part.org_id is None or refs is None or in_scope(t, refs)
    }
    return want, refs


async def _project(db: AsyncSession, part: Partition, now: datetime) -> Projection:
    snapshot = await load_snapshot(db, tenant_id=part.tenant_id or PLATFORM)
    return _expected(snapshot, part, now)


def _ops(diff: TupleDiff, mode: Mode) -> list[TupleOp]:
    if mode == "fill":
        return [TupleOp("write", t) for t in diff.missing]
    ops = [TupleOp("delete", t) for t in diff.extra]
    ops += [TupleOp("write", t, replace=True) for t in diff.changed]
    return ops + [TupleOp("write", t) for t in diff.missing]


async def _partition(
    sessions: Sessions,
    index: StoredIndex,
    part: Partition,
    *,
    mode: Mode,
    started: datetime,
    job_id: UUID,
    now: datetime,
) -> tuple[TupleDiff, int, int, list[UUID]]:
    async with sessions() as db:
        want, refs = await _project(db, part, now)
    have = index.claim(refs, want)
    diff = diff_tuples(want, have)
    if not diff:
        return diff, len(want), len(have), []
    async with sessions() as db:
        keys = sorted({tuple_key(t) for t in (*diff.missing, *diff.extra, *diff.changed)})
        recent = await holds.recent_keys(db, keys, started, job_id)
        diff = hold(diff, recent, await holds.recent_markers(db, started))
        ids: list[UUID] = []
        if mode != "dry_run" and diff:
            diff = recheck(diff, (await _project(db, part, now))[0])
            ops = _ops(diff, mode)
            ids = await enqueue(db, ops, source_table=SOURCE_TABLE, source_id=job_id, now=now)
            await db.commit()
    return diff, len(want), len(have), ids


async def _drain(sessions: Sessions, store: TupleStore, ids: list[UUID], now: datetime) -> int:
    failed = 0
    for chunk in batched(ids, MAX_BATCH):
        run = await process_outbox(sessions, store, ids=chunk, limit=len(chunk), now=now)
        failed += len(chunk) - len(run.processed)
    return failed


async def reconcile_store(
    sessions: Sessions,
    store: TupleStore,
    *,
    mode: Mode = "dry_run",
    tenant_id: UUID | None = None,
    org_id: UUID | None = None,
    drain: bool = False,
    job_id: UUID | None = None,
    now: datetime | None = None,
) -> ReconcileReport:
    """One run; ``drain`` applies the queued rows before returning (``failed`` counts rest)."""
    stamp = now or datetime.now(UTC)
    started, job = stamp - holds.HOLD_MARGIN, job_id or uuid4()
    async with sessions() as db:
        parts = await holds.partitions(db, tenant_id, org_id)
    label = parts[0].label if len(parts) == 1 else "store"
    index = StoredIndex({key_of(t): t async for t in store.read_all()})
    report, ids = ReconcileReport(scope=label, mode=mode), []
    for part in parts:
        diff, want, have, queued = await _partition(
            sessions, index, part, mode=mode, started=started, job_id=job, now=stamp
        )
        report, ids = report.add(diff, want, have), [*ids, *queued]
    failed = await _drain(sessions, store, ids, stamp) if drain else 0
    report = report.model_copy(update={"queued": len(ids), "outbox_ids": ids, "failed": failed})
    logger.info(
        "authz_reconcile_diff",
        extra={"scope": label, "mode": mode, "diff": report.diff, "held": report.held},
    )
    return report


def reconcile_sessions(maker: Callable[[], AsyncSession] | None = None) -> Sessions:
    """Platform scope: a run reads every company's rows and the instance-wide queues."""
    if maker is None:
        return platform_sessions("authz.reconcile")
    return platform_sessions("authz.reconcile", maker=maker)


async def run_reconcile_job(
    sessions: Sessions, store: TupleStore, *, now: datetime | None = None
) -> ReconcileReport:
    """The daily ``authz_reconcile`` body (C-05 registers it); the outbox drain applies it."""
    return await reconcile_store(sessions, store, mode="repair", now=now)
