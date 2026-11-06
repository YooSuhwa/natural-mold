"""Queue operations on ``jobs`` rows: enqueue, claim, heartbeat, finish, retry, cancel.

* Enqueue runs in the requester's company session; the same ``(type, idempotency_key)``
  in a company returns the existing job instead of a second one.
* Claim runs in a platform session (the worker serves every company): one row is taken
  with ``FOR UPDATE SKIP LOCKED`` so two workers never take the same job, and a running
  row whose lease ran out (its worker died) is taken again as a new attempt.
* Everything after the claim (heartbeat, progress, result) is written in the job's company
  session, and only by the worker that holds the claim (``locked_by``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.exceptions import NotFoundError
from app.models.job import TERMINAL_STATES, Job, JobWorker

# Retry delays grow with each attempt and stop growing at the last one.
_BACKOFF_SECONDS: Final = (10, 60, 300, 900)
_MESSAGE_LIMIT: Final = 500


def is_finished(state: str) -> bool:
    return state in TERMINAL_STATES


async def owned_job(db: AsyncSession, job_id: uuid.UUID, context: OrgContext) -> Job:
    """The requester's own job in their organization; anything else is the same 404."""
    job = await db.scalar(
        select(Job).where(
            Job.id == job_id,
            Job.tenant_id == context.tenant_id,
            Job.org_id == context.org_id,
            Job.actor_id == context.user_id,
        )
    )
    if job is None:
        raise NotFoundError("JOB_NOT_FOUND", "작업을 찾을 수 없습니다")
    return job


def now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite gives timestamps back without a zone; they were written in UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class EnqueueRequest:
    type: str
    payload: dict[str, Any]
    resource_id: str | None = None
    idempotency_key: str | None = None
    max_attempts: int = 3
    run_after: datetime | None = None


async def enqueue(
    db: AsyncSession, context: OrgContext, request: EnqueueRequest, *, actor_id: uuid.UUID | None
) -> Job:
    """Add a job for ``context``'s company and organization (committed by the caller)."""
    if request.idempotency_key is not None:
        existing = await _by_key(db, context.tenant_id, request.type, request.idempotency_key)
        if existing is not None:
            return existing
    job = Job(
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        actor_id=actor_id,
        type=request.type,
        resource_id=request.resource_id,
        idempotency_key=request.idempotency_key,
        payload=request.payload,
        max_attempts=request.max_attempts,
        run_after=request.run_after or now(),
    )
    try:
        async with db.begin_nested():
            db.add(job)
            await db.flush()
    except IntegrityError:
        # Another request enqueued the same key at the same time: return that one.
        if request.idempotency_key is None:
            raise
        existing = await _by_key(db, context.tenant_id, request.type, request.idempotency_key)
        if existing is None:
            raise
        return existing
    return job


async def _by_key(db: AsyncSession, tenant_id: uuid.UUID, job_type: str, key: str) -> Job | None:
    return await db.scalar(
        select(Job).where(
            Job.tenant_id == tenant_id, Job.type == job_type, Job.idempotency_key == key
        )
    )


@dataclass(frozen=True, slots=True)
class Claim:
    """What the worker needs to open the job's company session."""

    job_id: uuid.UUID
    tenant_id: uuid.UUID
    org_id: uuid.UUID
    actor_id: uuid.UUID | None
    type: str
    attempt: int
    lease_until: datetime


async def claim(
    db: AsyncSession,
    *,
    worker_id: str,
    types: tuple[str, ...],
    lease: dict[str, int],
    at: datetime | None = None,
) -> Claim | None:
    """Take the next due job of ``types`` (platform session; commits).

    A job that has used up its attempts when its lease ran out is failed here instead of
    being run again.
    """
    if not types:
        return None
    moment = at or now()
    due = or_(
        and_(Job.state == "queued", Job.run_after <= moment),
        and_(Job.state == "running", Job.lease_until < moment),
    )
    while True:
        job = await db.scalar(
            select(Job)
            .where(Job.type.in_(types), due)
            .order_by(Job.run_after, Job.created_at, Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            await db.commit()
            return None
        if job.state == "running" and job.attempts >= job.max_attempts:
            _finish(job, "failed", code="lease_expired", message="the worker stopped responding")
            await db.commit()
            continue
        if job.cancel_requested:
            _finish(job, "canceled")
            await db.commit()
            continue
        lease_until = moment + timedelta(seconds=lease.get(job.type, 120))
        job.state = "running"
        job.attempts += 1
        job.locked_by = worker_id
        job.lease_until = lease_until
        job.heartbeat_at = moment
        result = Claim(
            job.id, job.tenant_id, job.org_id, job.actor_id, job.type, job.attempts, lease_until
        )
        await db.commit()
        return result


async def _held(db: AsyncSession, job_id: uuid.UUID, worker_id: str) -> Job | None:
    """The job if this worker still holds it (a recovered lease belongs to someone else)."""
    return await db.scalar(
        select(Job).where(Job.id == job_id, Job.locked_by == worker_id, Job.state == "running")
    )


async def heartbeat(
    db: AsyncSession,
    job_id: uuid.UUID,
    worker_id: str,
    *,
    lease_seconds: int,
    progress: int | None = None,
    note: str | None = None,
) -> bool | None:
    """Extend the lease (company session; commits). ``None``: the claim was lost.

    Returns whether someone asked to cancel the job.
    """
    job = await _held(db, job_id, worker_id)
    if job is None:
        await db.commit()
        return None
    moment = now()
    job.heartbeat_at = moment
    job.lease_until = moment + timedelta(seconds=lease_seconds)
    if progress is not None:
        job.progress = max(0, min(100, progress))
    if note is not None:
        job.progress_note = note[:200]
    canceled = job.cancel_requested
    await db.commit()
    return canceled


def _finish(
    job: Job,
    state: str,
    *,
    result: dict[str, Any] | None = None,
    code: str | None = None,
    message: str | None = None,
) -> None:
    job.state = state
    job.locked_by = None
    job.lease_until = None
    job.finished_at = now()
    if state == "succeeded":
        job.progress = 100
        job.result = result
    job.error_code = code
    job.error_message = message[:_MESSAGE_LIMIT] if message else None


async def succeed(
    db: AsyncSession, job_id: uuid.UUID, worker_id: str, result: dict[str, Any] | None
) -> bool:
    job = await _held(db, job_id, worker_id)
    if job is None:
        await db.commit()
        return False
    _finish(job, "succeeded", result=result)
    await db.commit()
    return True


async def fail(
    db: AsyncSession,
    job_id: uuid.UUID,
    worker_id: str,
    *,
    code: str,
    message: str,
    retry: bool,
) -> str | None:
    """Queue another attempt after a delay, or end the job. Returns the new state."""
    job = await _held(db, job_id, worker_id)
    if job is None:
        await db.commit()
        return None
    if retry and job.attempts < job.max_attempts and not job.cancel_requested:
        delay = _BACKOFF_SECONDS[min(job.attempts - 1, len(_BACKOFF_SECONDS) - 1)]
        job.state = "queued"
        job.locked_by = None
        job.lease_until = None
        job.run_after = now() + timedelta(seconds=delay)
        job.error_code = code
        job.error_message = message[:_MESSAGE_LIMIT]
    else:
        _finish(job, "failed", code=code, message=message)
    state = job.state
    await db.commit()
    return state


async def canceled(db: AsyncSession, job_id: uuid.UUID, worker_id: str) -> bool:
    job = await _held(db, job_id, worker_id)
    if job is None:
        await db.commit()
        return False
    _finish(job, "canceled")
    await db.commit()
    return True


async def request_cancel(db: AsyncSession, job: Job) -> Job:
    """A queued job ends now; a running one is told on its next heartbeat (caller commits)."""
    if job.state in TERMINAL_STATES:
        return job
    if job.state == "queued":
        _finish(job, "canceled")
    else:
        job.cancel_requested = True
    await db.flush()
    return job


async def record_worker(
    db: AsyncSession, worker_id: str, *, hostname: str, types: tuple[str, ...], started: datetime
) -> None:
    """The worker's own heartbeat row (platform session; commits)."""
    row = await db.get(JobWorker, worker_id)
    moment = now()
    if row is None:
        db.add(
            JobWorker(
                worker_id=worker_id,
                hostname=hostname,
                job_types=list(types),
                started_at=started,
                heartbeat_at=moment,
            )
        )
    else:
        row.heartbeat_at = moment
        row.job_types = list(types)
    await db.commit()


async def forget_worker(db: AsyncSession, worker_id: str) -> None:
    row = await db.get(JobWorker, worker_id)
    if row is not None:
        await db.delete(row)
    await db.commit()


def heartbeat_age(row: JobWorker, *, at: datetime | None = None) -> timedelta:
    beat = _aware(row.heartbeat_at) or now()
    return (at or now()) - beat


@dataclass(frozen=True, slots=True)
class QueueSummary:
    queued: int
    running: int
    overdue: int  # queued and due for longer than OVERDUE
    failed_last_day: int


OVERDUE: Final = timedelta(minutes=1)


async def workers(db: AsyncSession) -> list[JobWorker]:
    """Every worker heartbeat row, newest beat first (platform session)."""
    rows = await db.execute(select(JobWorker).order_by(JobWorker.heartbeat_at.desc()))
    return list(rows.scalars())


async def queue_summary(db: AsyncSession, *, at: datetime | None = None) -> QueueSummary:
    """Counts across every company (platform session): what the operator status shows."""
    moment = at or now()
    rows = await db.execute(
        select(
            func.count().filter(Job.state == "queued"),
            func.count().filter(Job.state == "running"),
            func.count().filter(and_(Job.state == "queued", Job.run_after < moment - OVERDUE)),
            func.count().filter(
                and_(Job.state == "failed", Job.finished_at >= moment - timedelta(days=1))
            ),
        )
    )
    queued, running, overdue, failed = rows.one()
    return QueueSummary(int(queued), int(running), int(overdue), int(failed))
