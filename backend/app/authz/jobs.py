"""C-05 leader jobs: outbox drain, daily reconcile, expiry cleanup, backlog alert (8.6, 14.3)
and the F-03 daily delegation validator (10.6).

``app.scheduler.register_authz_jobs`` registers them and the fenced
``run_leader_scheduler_job`` dispatches them. Nothing is scheduled and every run is a
no-op unless ``AUTHZ_MODE`` is shadow or enforce with a pinned OpenFGA store and model,
so the default legacy mode never calls OpenFGA.

Leader handoff: a fenced old leader may still be inside a run while the new leader
starts. No row is processed twice: the drain claims rows ``FOR UPDATE SKIP LOCKED`` and
marks them processed in the same transaction (a batch that dies before its commit is
retried by the next leader; OpenFGA writes and deletes are idempotent); expiry re-reads
each grant under its object lock and skips one another worker already closed; pruning
and alerting are idempotent.
"""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import and_, delete, func, or_, select

from app.authz.client import open_client
from app.authz.config import AuthzSettings
from app.authz.delegation_retry import revalidate_finalized
from app.authz.delegation_sweep import run_delegation_sweep
from app.authz.grants import GrantService
from app.authz.outbox import ALERT_ATTEMPTS, OutboxRun, Sessions, TupleWriter, process_outbox
from app.authz.reconcile import TupleStore, reconcile_sessions, run_reconcile_job
from app.authz.revocations import oldest_pending_at
from app.authz.runtime import runtime_authz
from app.database import platform_sessions
from app.models.authz import AuthzOutbox, AuthzRevocation, ResourceGrant
from app.scheduler_job_ids import LeaderSchedulerJobId as JobId

logger = logging.getLogger(__name__)
JOBS: tuple[JobId, ...] = (
    JobId.AUTHZ_OUTBOX_DRAIN,
    JobId.AUTHZ_RECONCILE,
    JobId.AUTHZ_EXPIRY,
    JobId.AUTHZ_ALERT,
    JobId.AUTHZ_DELEGATION_VALIDATE,
)
MAINTENANCE_SECONDS = 60
EXPIRY_BATCH = 100
PRUNE_CHUNK = 1000  # rows per table per tick
RETENTION = timedelta(days=7)  # processed outbox rows and finalized markers
STUCK_MARKER_AFTER = timedelta(minutes=10)
Opener = Callable[[], AbstractAsyncContextManager[TupleStore]]


@dataclass(frozen=True, slots=True)
class Backlog:
    """14.3 gauges, logged under these names."""

    authz_outbox_pending: int
    authz_outbox_failed: int  # pending rows at or past the alert attempt count
    authz_outbox_lag_seconds: float
    authz_revocation_oldest_seconds: float


def jobs_enabled(config: AuthzSettings) -> bool:
    pinned = bool(config.openfga_store_id and config.openfga_model_id)
    return config.authz_mode != "legacy" and pinned


def schedule(config: AuthzSettings) -> list[tuple[JobId, BaseTrigger]]:
    """The leader jobs to register; empty in legacy mode or without a pinned model."""
    if not jobs_enabled(config):
        if config.authz_mode != "legacy":
            logger.warning("authz jobs not scheduled: set OPENFGA_STORE_ID and OPENFGA_MODEL_ID")
        return []
    every = IntervalTrigger(seconds=MAINTENANCE_SECONDS)
    poll = max(int(config.authz_outbox_poll_seconds), 1)  # whole seconds, as APScheduler types it
    found: list[tuple[JobId, BaseTrigger]] = [
        (JobId.AUTHZ_OUTBOX_DRAIN, IntervalTrigger(seconds=poll)),
        (JobId.AUTHZ_EXPIRY, every),
        (JobId.AUTHZ_ALERT, every),
    ]
    daily = (
        (JobId.AUTHZ_RECONCILE, "authz_reconcile_cron"),
        (JobId.AUTHZ_DELEGATION_VALIDATE, "authz_delegation_cron"),  # F-03 (10.6)
    )
    for job, name in daily:
        cron = getattr(config, name)
        try:
            found.append((job, CronTrigger.from_crontab(cron, timezone=UTC)))
        except ValueError:
            logger.exception("invalid %s=%r; %s not scheduled", name, cron, job.value)
    return found


async def drain_outbox(
    sessions: Sessions, writer: TupleWriter, *, now: datetime | None = None
) -> OutboxRun:
    """8.6 item 6: one batch (100 rows) per tick; backoff and alerts live in the outbox."""
    return await process_outbox(sessions, writer, now=now)


async def has_due_rows(sessions: Sessions, *, now: datetime | None = None) -> bool:
    """Cheap pre-check so an idle tick never opens an OpenFGA client."""
    async with sessions() as db:
        found = await db.scalar(
            select(AuthzOutbox.id)
            .where(
                AuthzOutbox.processed_at.is_(None),
                AuthzOutbox.next_attempt_at <= (now or datetime.now(UTC)),
            )
            .limit(1)
        )
    return found is not None


async def _due_page(
    sessions: Sessions, stamp: datetime, after: tuple[datetime, UUID] | None, limit: int
) -> list[tuple[datetime, UUID]]:
    query = select(ResourceGrant.expires_at, ResourceGrant.id).where(
        ResourceGrant.state == "active", ResourceGrant.expires_at <= stamp
    )
    if after is not None:
        query = query.where(
            or_(
                ResourceGrant.expires_at > after[0],
                and_(ResourceGrant.expires_at == after[0], ResourceGrant.id > after[1]),
            )
        )
    query = query.order_by(ResourceGrant.expires_at, ResourceGrant.id).limit(limit)
    async with sessions() as db:
        return [(expires, grant_id) for expires, grant_id in (await db.execute(query)).all()]


async def expire_grants(
    sessions: Sessions,
    writer: TupleWriter,
    *,
    now: datetime | None = None,
    limit: int = EXPIRY_BATCH,
) -> list[UUID]:
    """8.6 item 7: close up to ``limit`` grants past ``expires_at``, one transaction each.

    Pages by ``(expires_at, id)``: a grant that fails (logged, retried next tick) is never
    revisited in the same run, and later grants are still reached behind it.
    """
    stamp = now or datetime.now(UTC)
    service = GrantService(writer, sessions, clock=lambda: stamp)
    closed: list[UUID] = []
    after: tuple[datetime, UUID] | None = None
    while len(closed) < limit:
        page = await _due_page(sessions, stamp, after, limit)
        for _expires, grant_id in page:
            if len(closed) < limit and await _expire_one(service, sessions, grant_id):
                closed.append(grant_id)
        if len(page) < limit:
            break
        after = page[-1]
    if closed:
        logger.info("authz_grants_expired", extra={"count": len(closed)})
    return closed


async def _expire_one(service: GrantService, sessions: Sessions, grant_id: UUID) -> bool:
    try:
        async with sessions() as db:
            change = await service.expire(db, grant_id)
            if change is None:  # closed or extended by another worker: rolls back
                return False
            await db.commit()
    except Exception:  # one bad grant must not block the rest; retried next tick
        logger.exception("authz_grant_expiry_failed", extra={"grant_id": str(grant_id)})
        return False
    await service.apply(change)  # 8.6 item 9: its marker is re-validated once finalized
    return True


async def prune(
    sessions: Sessions, *, now: datetime | None = None, chunk: int = PRUNE_CHUNK
) -> tuple[int, int]:
    """Delete up to ``chunk`` processed outbox rows and finalized markers past ``RETENTION``.

    Both are history once closed; reconcile holds only look back ``HOLD_MARGIN``. Bounded
    per tick so a large backlog is worked off over several runs in short transactions.
    """
    cutoff = (now or datetime.now(UTC)) - RETENTION
    old_rows = select(AuthzOutbox.id).where(AuthzOutbox.processed_at < cutoff).limit(chunk)
    old_markers = (
        select(AuthzRevocation.id).where(AuthzRevocation.finalized_at < cutoff).limit(chunk)
    )
    async with sessions() as db:
        rows = await db.scalars(
            delete(AuthzOutbox)
            .where(AuthzOutbox.id.in_(old_rows.scalar_subquery()))
            .returning(AuthzOutbox.id)
        )
        outbox = len(rows.all())
        rows = await db.scalars(
            delete(AuthzRevocation)
            .where(AuthzRevocation.id.in_(old_markers.scalar_subquery()))
            .returning(AuthzRevocation.id)
        )
        markers = len(rows.all())
        await db.commit()
    return outbox, markers


def _age(since: datetime | None, now: datetime) -> float:
    if since is None:
        return 0.0
    aware = since if since.tzinfo else since.replace(tzinfo=UTC)
    return max((now - aware).total_seconds(), 0.0)


async def check_backlog(sessions: Sessions, *, now: datetime | None = None) -> Backlog:
    """14.3 lag metrics; a marker pending past ``STUCK_MARKER_AFTER`` keeps denying."""
    stamp = now or datetime.now(UTC)
    failing = func.count().filter(AuthzOutbox.attempts >= ALERT_ATTEMPTS)
    async with sessions() as db:
        found = await db.execute(
            select(func.count(), failing, func.min(AuthzOutbox.created_at)).where(
                AuthzOutbox.processed_at.is_(None)
            )
        )
        pending, failed, oldest = found.one()
        marker = await oldest_pending_at(db)
    backlog = Backlog(int(pending), int(failed), _age(oldest, stamp), _age(marker, stamp))
    logger.info("authz_backlog", extra=asdict(backlog))
    if backlog.authz_revocation_oldest_seconds > STUCK_MARKER_AFTER.total_seconds():
        logger.error("authz_revocation_stuck", extra=asdict(backlog))
    return backlog


async def run_job(job: JobId, config: AuthzSettings, *, opener: Opener | None = None) -> None:
    """Scheduler entry point; keeps the job alive on any failure (logged)."""
    if not jobs_enabled(config) or job not in JOBS:
        return
    try:
        if job is JobId.AUTHZ_ALERT:
            await check_backlog(platform_sessions("authz.backlog_alert"))
            return
        if job is JobId.AUTHZ_DELEGATION_VALIDATE:
            await run_delegation_sweep(runtime_authz())
            return
        idle = job is JobId.AUTHZ_OUTBOX_DRAIN
        if idle and not await has_due_rows(platform_sessions("authz.outbox_drain")):
            return
        async with (opener or (lambda: open_client(config)))() as store:
            await _with_store(job, store)
    except Exception:  # noqa: BLE001 - keep the leader job alive
        logger.exception("authz_job_failed", extra={"job": job.value})


async def _with_store(job: JobId, store: TupleStore) -> None:
    if job is JobId.AUTHZ_OUTBOX_DRAIN:
        await drain_outbox(platform_sessions("authz.outbox_drain"), store)
    elif job is JobId.AUTHZ_RECONCILE:
        await run_reconcile_job(reconcile_sessions(), store)
    else:
        sessions = platform_sessions("authz.expiry_cleanup")
        await expire_grants(sessions, store)
        await prune(sessions)
        await revalidate_finalized(runtime_authz())  # F-03: settled revocations (10.6)
