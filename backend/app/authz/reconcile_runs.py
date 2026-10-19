"""Recorded reconcile runs for the operator sync screen (plan 14.3, S27; H-04).

* ``claim_run``: insert a ``queued`` row holding the single ``active`` slot (one live run
  across processes); ``None`` when another run holds it. A live run bumps
  ``updated_at`` every ``HEARTBEAT``; one silent for ``STALE_AFTER`` (its process died) is
  closed as ``abandoned`` first, and by ``close_stale`` on the leader's maintenance tick.
* ``execute_run``: ``running`` → ``reconcile_store(mode=repair)`` with per-company
  progress → ``succeeded`` / ``skipped`` (another repair held the advisory lock) /
  ``failed``; the slot is released in every outcome, cancellation included.
* ``scheduled_reconcile``: the C-05 daily job body, recorded the same way (no drain).

Operator runs drain their own outbox rows so the screen can show repaired counts.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.reconcile import ReconcileReport, Sessions, TupleStore, reconcile_store
from app.models.authz_reconcile_run import AuthzReconcileRun as Run
from app.schemas.admin_authz import ReconcileCounts, ReconcileRunView, ReconcileStatus
from app.services.cancel_deferred import finish_then_cancel

logger = logging.getLogger(__name__)
STALE_AFTER = timedelta(minutes=15)
HEARTBEAT = timedelta(seconds=60)
Trigger = Literal["manual", "scheduled"]


def _now() -> datetime:
    return datetime.now(UTC)


async def _close_stale(db: AsyncSession, now: datetime) -> None:
    await db.execute(
        update(Run)
        .where(Run.active.is_(True), Run.updated_at < now - STALE_AFTER)
        .values(status="failed", active=None, error="abandoned", finished_at=now, updated_at=now)
    )


async def close_stale(sessions: Sessions) -> None:
    """Leader start-up / maintenance: release the slot of runs whose process died."""
    async with sessions() as db:
        await _close_stale(db, _now())
        await db.commit()


async def _heartbeat(sessions: Sessions, run_id: UUID) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT.total_seconds())
        try:  # ``_stop`` cancels this task; a cancel never tears a beat mid-statement
            await finish_then_cancel(_set(sessions, run_id))
        except Exception:  # noqa: BLE001 - a missed beat only shortens the margin
            logger.warning("authz_reconcile_heartbeat_failed", extra={"run_id": str(run_id)})


async def claim_run(
    db: AsyncSession, *, trigger: Trigger, requested_by: UUID | None = None
) -> Run | None:
    """Flush a queued run in ``db`` (caller commits); ``None`` while another is live."""
    now = _now()
    await _close_stale(db, now)
    if await db.scalar(select(Run.id).where(Run.active.is_(True))) is not None:
        return None
    run = Run(
        id=uuid4(),
        status="queued",
        trigger=trigger,
        mode="repair",
        active=True,
        requested_by=requested_by,
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    try:
        await db.flush()
    except IntegrityError:  # another process claimed the slot in between
        await db.rollback()
        return None
    return run


async def _set(sessions: Sessions, run_id: UUID, **values: object) -> None:
    async with sessions() as db:
        await db.execute(update(Run).where(Run.id == run_id).values(updated_at=_now(), **values))
        await db.commit()


def _counts(report: ReconcileReport, *, drained: bool) -> dict[str, int]:
    counts = ReconcileCounts(
        **report.model_dump(include={"expected", "stored", "missing", "extra", "changed", "held"}),
        queued=report.queued,
        failed=report.failed,
        diff=report.diff,
        repaired=report.queued - report.failed if drained else 0,
        deferred=report.failed if drained else report.queued,
    )
    return counts.model_dump()


async def execute_run(
    sessions: Sessions,
    store: TupleStore,
    run_id: UUID,
    *,
    drain: bool,
    now: datetime | None = None,
) -> ReconcileReport | None:
    """Run and record one claimed reconcile; never raises except cancellation."""
    await _set(sessions, run_id, status="running", started_at=_now())

    async def progress(done: int, total: int) -> None:
        await _set(sessions, run_id, partitions_done=done, partitions_total=total)

    beat = asyncio.create_task(_heartbeat(sessions, run_id))
    try:
        report = await reconcile_store(
            sessions, store, mode="repair", drain=drain, job_id=run_id, now=now, progress=progress
        )
    except asyncio.CancelledError:
        await asyncio.shield(_stop(beat))
        await asyncio.shield(_finish(sessions, run_id, "failed", error="cancelled"))
        raise
    except Exception as exc:  # noqa: BLE001 - recorded on the run, alert logged
        logger.exception("authz_reconcile_failed", extra={"run_id": str(run_id)})
        await _stop(beat)
        await _finish(sessions, run_id, "failed", error=type(exc).__name__)
        return None
    await _stop(beat)  # the final status is written after the last beat, never beside it
    status = "skipped" if report.skipped else "succeeded"
    await _finish(sessions, run_id, status, report=_counts(report, drained=drain))
    return report


async def _stop(beat: asyncio.Task[None]) -> None:
    beat.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await beat


async def _finish(
    sessions: Sessions,
    run_id: UUID,
    status: str,
    *,
    error: str | None = None,
    report: dict[str, int] | None = None,
) -> None:
    values: dict[str, object] = {"status": status, "active": None, "finished_at": _now()}
    if report is not None:
        values["report"] = report
    if error is not None:
        values["error"] = error[:200]
    if status == "succeeded":
        values["partitions_done"] = Run.partitions_total
    await _set(sessions, run_id, **values)


async def fail_run(sessions: Sessions, run_id: UUID, error: str) -> None:
    """Close a still-live run whose OpenFGA client failed; a finished run is kept."""
    async with sessions() as db:
        await db.execute(
            update(Run)
            .where(Run.id == run_id, Run.active.is_(True))
            .values(
                status="failed",
                active=None,
                error=error[:200],
                finished_at=_now(),
                updated_at=_now(),
            )
        )
        await db.commit()


async def scheduled_reconcile(sessions: Sessions, store: TupleStore) -> ReconcileReport | None:
    """The daily job: recorded like an operator run, rows left to the outbox drain."""
    async with sessions() as db:
        run = await claim_run(db, trigger="scheduled")
        if run is None:
            logger.warning("authz_reconcile_skipped", extra={"reason": "run_in_progress"})
            return None
        run_id = run.id
        await db.commit()
    return await execute_run(sessions, store, run_id, drain=False)


def view(run: Run) -> ReconcileRunView:
    report = ReconcileCounts.model_validate(run.report) if run.report else None
    return ReconcileRunView.model_validate(
        {
            **{name: getattr(run, name) for name in ReconcileRunView.model_fields},
            "report": report,
        }
    )


async def get_run(db: AsyncSession, run_id: UUID) -> Run | None:
    return await db.get(Run, run_id, populate_existing=True)


async def reconcile_status(db: AsyncSession) -> ReconcileStatus:
    active = await db.scalar(select(Run).where(Run.active.is_(True)))
    last = await db.scalar(
        select(Run)
        .where(Run.active.is_(None))
        .order_by(Run.created_at.desc(), Run.id.desc())
        .limit(1)
    )
    return ReconcileStatus(
        active=view(active) if active else None, last=view(last) if last else None
    )
