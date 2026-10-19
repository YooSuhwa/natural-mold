"""Operator authorization-sync status (plan 14.3, S27; H-04): read-only aggregates.

Counts only: outbox backlog (C-05 gauges), stuck revocation markers, the last and the
live reconcile run, shadow disagreements by object type, broken delegation links (F-03),
the retention sweep (E-08) and which leader jobs this configuration schedules.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions, jobs
from app.authz.ops_health import Opener, fga_health
from app.authz.reconcile_runs import reconcile_status
from app.config import Settings
from app.models.agent_subagent import AgentSubAgentLink
from app.models.audit_event import AuditEvent
from app.models.authz import AuthzOutbox, AuthzRevocation, AuthzShadowDiff
from app.models.mcp_tool import AgentMcpToolLink
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink
from app.scheduler_job_ids import LeaderSchedulerJobId as JobId
from app.schemas.admin_authz import (
    AuthzOpsStatus,
    DelegationStats,
    JobStatus,
    OutboxStats,
    RetentionStats,
    RevocationStats,
    ShadowTypeCount,
)

LINK_MODELS = (AgentToolLink, AgentSkillLink, AgentMcpToolLink, AgentSubAgentLink)
DAY = timedelta(days=1)


async def _count(db: AsyncSession, query: Select[tuple[int]]) -> int:
    return int(await db.scalar(query) or 0)


async def _outbox(db: AsyncSession, now: datetime) -> tuple[OutboxStats, float]:
    backlog = await jobs.read_backlog(db, now)
    retrying = select(func.count()).where(
        AuthzOutbox.processed_at.is_(None), AuthzOutbox.attempts >= 1
    )
    return OutboxStats(
        pending=backlog.authz_outbox_pending,
        retrying=await _count(db, retrying),
        failed=backlog.authz_outbox_failed,
        lag_seconds=backlog.authz_outbox_lag_seconds,
    ), backlog.authz_revocation_oldest_seconds


async def _revocations(db: AsyncSession, now: datetime, oldest: float) -> RevocationStats:
    pending = AuthzRevocation.finalized_at.is_(None)
    stuck_before = now - jobs.STUCK_MARKER_AFTER
    return RevocationStats(
        pending=await _count(db, select(func.count()).where(pending)),
        stuck=await _count(
            db, select(func.count()).where(pending, AuthzRevocation.created_at < stuck_before)
        ),
        oldest_seconds=oldest,
    )


async def _shadow(db: AsyncSession) -> list[ShadowTypeCount]:
    rows = await db.execute(
        select(AuthzShadowDiff.object, AuthzShadowDiff.legacy, func.sum(AuthzShadowDiff.count))
        .where(AuthzShadowDiff.legacy != AuthzShadowDiff.fga)
        .group_by(AuthzShadowDiff.object, AuthzShadowDiff.legacy)
    )
    totals: dict[str, list[int]] = {}
    for obj, legacy, count in rows.all():
        pair = totals.setdefault(obj.split(":", 1)[0], [0, 0])
        pair[0 if legacy else 1] += int(count or 0)
    return [
        ShadowTypeCount(type=name, legacy_only=pair[0], fga_only=pair[1])
        for name, pair in sorted(totals.items())
    ]


async def _delegations(db: AsyncSession, since: datetime) -> DelegationStats:
    broken = 0
    for model in LINK_MODELS:
        query = select(func.count()).select_from(model).where(model.link_status == "broken")
        broken += await _count(db, query)
    events = select(func.count()).where(
        AuditEvent.action == audit_actions.DELEGATION_BROKEN, AuditEvent.created_at >= since
    )
    return DelegationStats(broken_links=broken, broken_last_24h=await _count(db, events))


async def _retention(db: AsyncSession, app: Settings, since: datetime) -> RetentionStats:
    purge = AuditEvent.action == audit_actions.RETENTION_PURGE
    last = await db.scalar(select(func.max(AuditEvent.created_at)).where(purge))
    recent = select(func.count()).where(purge, AuditEvent.created_at >= since)
    return RetentionStats(
        enabled=app.retention_sweep_enabled,
        schedule=app.retention_sweep_cron,
        last_run_at=last.replace(tzinfo=UTC) if last and last.tzinfo is None else last,
        purges_last_24h=await _count(db, recent),
    )


def _jobs(app: Settings) -> list[JobStatus]:
    planned = {job: str(trigger) for job, trigger in jobs.schedule(app)}
    found = [
        JobStatus(id=job.value, scheduled=job in planned, schedule=planned.get(job))
        for job in jobs.JOBS
    ]
    sweep = JobId.RETENTION_SWEEP
    on = app.retention_sweep_enabled
    found.append(
        JobStatus(id=sweep.value, scheduled=on, schedule=app.retention_sweep_cron if on else None)
    )
    return found


async def ops_status(
    db: AsyncSession, app: Settings, *, opener: Opener | None = None
) -> AuthzOpsStatus:
    now = datetime.now(UTC)
    naive_day = (now - DAY).replace(tzinfo=None)  # audit_events.created_at is naive UTC
    outbox, oldest = await _outbox(db, now)
    health = await fga_health(app, opener=opener)
    return AuthzOpsStatus(
        mode=app.authz_mode,
        sync_enabled=jobs.jobs_enabled(app),
        openfga=health,
        outbox=outbox,
        revocations=await _revocations(db, now, oldest),
        reconcile=await reconcile_status(db),
        shadow=await _shadow(db),
        delegations=await _delegations(db, naive_day),
        retention=await _retention(db, app, naive_day),
        jobs=_jobs(app),
    )
