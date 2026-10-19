"""C-05 leader jobs: drain, expiry, pruning, backlog alert and leader handoff (8.6)."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import jobs
from app.authz.config import AuthzSettings
from app.authz.fake import FakeTupleStore
from app.authz.grants import GrantRequest
from app.authz.revocations import count_pending, record_revocation
from app.models.audit_event import AuditEvent
from app.models.authz import AuthzOutbox, AuthzRevocation, ResourceGrant
from app.scheduler_job_ids import LeaderSchedulerJobId as JobId
from tests.authz.grant_testkit import Clock, agent_target, count, outbox_rows, seed_world, service
from tests.authz.revocation_testkit import _fresh_pending_hint  # noqa: F401 - autouse
from tests.conftest import TestSession

PINNED = {"openfga_store_id": "store", "openfga_model_id": "model"}


def config(mode: str = "shadow", **values: object) -> AuthzSettings:
    return AuthzSettings.model_validate({"authz_mode": mode, **PINNED, **values})


async def _queued(db: AsyncSession, clock: Clock, *, expires: timedelta | None = None):
    """A committed grant whose outbox rows were never applied (the drain's work)."""
    world, store = await seed_world(db), FakeTupleStore()
    target = await agent_target(db, world)
    expiry = clock.now + expires if expires is not None else None
    request = GrantRequest("consumer", "user", world.member, expires_at=expiry)
    change = await service(store, clock).grant(db, world.ctx, target, request)
    await db.commit()
    return world, change


def test_legacy_and_unpinned_modes_schedule_nothing(caplog: pytest.LogCaptureFixture):
    assert jobs.schedule(config("legacy")) == []
    with caplog.at_level(logging.WARNING):
        assert jobs.schedule(config(openfga_store_id="")) == []
    assert "OPENFGA_STORE_ID" in caplog.text
    planned = dict(jobs.schedule(config(authz_outbox_poll_seconds=2)))
    assert set(planned) == set(jobs.JOBS)
    assert planned[JobId.AUTHZ_OUTBOX_DRAIN].interval == timedelta(seconds=2)  # type: ignore[attr-defined]
    assert str(planned[JobId.AUTHZ_RECONCILE].timezone) == "UTC"  # type: ignore[attr-defined]
    assert JobId.AUTHZ_RECONCILE not in dict(jobs.schedule(config(authz_reconcile_cron="bad")))


@pytest.mark.parametrize("job", jobs.JOBS)
async def test_legacy_runs_touch_neither_openfga_nor_the_database(
    job: JobId, monkeypatch: pytest.MonkeyPatch
):
    opened: list[str] = []
    monkeypatch.setattr(jobs, "platform_sessions", lambda reason: opened.append(reason))

    def opener():
        opened.append("openfga")
        raise AssertionError("legacy mode must not call OpenFGA")

    await jobs.run_job(job, config("legacy"), opener=opener)
    await jobs.run_job(job, config(openfga_model_id=""), opener=opener)
    assert opened == []


async def test_drain_job_applies_queued_rows(db: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("app.database.async_session", TestSession)
    clock = Clock()
    clock.now -= timedelta(days=365)  # the job runs on the wall clock
    _world, change = await _queued(db, clock)
    store = FakeTupleStore()

    @asynccontextmanager
    async def opener() -> AsyncIterator[FakeTupleStore]:
        yield store

    await jobs.run_job(JobId.AUTHZ_OUTBOX_DRAIN, config(), opener=opener)
    [row] = await outbox_rows(db)
    assert row.id in change.outbox_ids and row.processed_at is not None
    assert len(store.stored()) == 1


class _LeaderLost(FakeTupleStore):
    """The old leader writes to OpenFGA, then is cancelled before its commit."""

    async def write(self, tuples):
        await super().write(tuples)
        raise asyncio.CancelledError


async def test_handoff_mid_batch_reapplies_idempotently_and_once(db: AsyncSession):
    clock = Clock()
    _world, change = await _queued(db, clock)
    old = _LeaderLost()
    with pytest.raises(asyncio.CancelledError):
        await jobs.drain_outbox(TestSession, old, now=clock.tick())
    [row] = await outbox_rows(db)
    assert row.processed_at is None and row.attempts == 0  # rolled back with the old leader
    new = FakeTupleStore(tuples=dict(old.tuples))
    run = await jobs.drain_outbox(TestSession, new, now=clock.tick())
    assert run.processed == change.outbox_ids and new.stored() == old.stored()
    processed_at = (await outbox_rows(db))[0].processed_at
    again = await jobs.drain_outbox(TestSession, new, now=clock.tick())
    assert again.processed == () and len(new.calls) == 1  # nothing processed twice
    assert (await outbox_rows(db))[0].processed_at == processed_at


async def test_expiry_closes_due_grants_once_across_leaders(db: AsyncSession):
    clock = Clock()
    _world, change = await _queued(db, clock, expires=timedelta(hours=1))
    _other, fresh = await _queued(db, clock, expires=timedelta(days=2))
    store = FakeTupleStore()
    await jobs.drain_outbox(TestSession, store, now=clock.now)
    assert len(store.stored()) == 2
    later = clock.tick(2 * 3600)
    old_leader, new_leader = FakeTupleStore(tuples=dict(store.tuples)), store
    assert await jobs.expire_grants(TestSession, new_leader, now=later) == [change.grant.id]
    assert await jobs.expire_grants(TestSession, old_leader, now=later) == []
    grant = await db.get(ResourceGrant, change.grant.id, populate_existing=True)
    assert grant is not None and grant.state == "revoked" and grant.revoked_by is None
    assert len(new_leader.stored()) == 1 and await count_pending(db) == 0
    kept = await db.get(ResourceGrant, fresh.grant.id, populate_existing=True)
    assert kept is not None and kept.state == "active"
    [event] = (
        await db.scalars(select(AuditEvent).where(AuditEvent.action == "grant.expire"))
    ).all()
    assert event.actor_type == "system" and event.actor_user_id is None
    assert await count(db, AuthzRevocation) == 1 and len(await outbox_rows(db)) == 3
    retry = service(old_leader, Clock())
    assert await retry.expire(db, change.grant.id) is None


async def test_expiry_failure_leaves_grant_for_the_next_tick(db: AsyncSession):
    clock = Clock()
    _world, change = await _queued(db, clock, expires=timedelta(hours=1))
    await jobs.drain_outbox(TestSession, FakeTupleStore(), now=clock.now)
    store = FakeTupleStore(fail_calls=1)  # immediate apply fails; the drain retries later
    later = clock.tick(7200)
    assert await jobs.expire_grants(TestSession, store, now=later) == [change.grant.id]
    grant = await db.get(ResourceGrant, change.grant.id, populate_existing=True)
    assert grant is not None and grant.state == "revoking" and await count_pending(db) == 1
    await jobs.drain_outbox(TestSession, store, now=clock.tick(60))
    await db.refresh(grant)
    assert grant.state == "revoked" and await count_pending(db) == 0


async def test_prune_keeps_pending_and_recent_history(db: AsyncSession):
    clock = Clock()
    world, _change = await _queued(db, clock)
    await jobs.drain_outbox(TestSession, FakeTupleStore(), now=clock.now)
    await _queued(db, clock)  # a second, still pending row
    marker = await record_revocation(
        db, org_id=world.ctx.org_id, tenant_id=world.tenant, scope="grant", subject="u", object="o"
    )
    pending = await record_revocation(
        db, org_id=world.ctx.org_id, tenant_id=world.tenant, scope="grant", subject="v", object="o"
    )
    marker.finalized_at = clock.now
    await db.commit()
    assert await jobs.prune(TestSession, now=clock.now + timedelta(days=6)) == (0, 0)
    assert await jobs.prune(TestSession, now=clock.now + timedelta(days=8)) == (1, 1)
    [left] = await outbox_rows(db)
    assert left.processed_at is None
    assert [m.id for m in (await db.scalars(select(AuthzRevocation))).all()] == [pending.id]


async def test_backlog_alerts_on_a_stuck_marker(db: AsyncSession, caplog):
    clock = Clock()
    world, _change = await _queued(db, clock)
    await _queued(db, clock)
    marker = await record_revocation(
        db, org_id=world.ctx.org_id, tenant_id=world.tenant, scope="grant", subject="u", object="o"
    )
    row, healthy = await outbox_rows(db)
    row.attempts = 10
    marker.created_at = row.created_at = clock.now
    healthy.created_at = clock.now + timedelta(minutes=1)
    await db.commit()
    with caplog.at_level(logging.INFO, logger="app.authz.jobs"):
        fresh = await jobs.check_backlog(TestSession, now=clock.now + timedelta(minutes=5))
        assert "authz_revocation_stuck" not in caplog.text
        stuck = await jobs.check_backlog(TestSession, now=clock.now + timedelta(minutes=11))
    assert fresh == jobs.Backlog(2, 1, 300.0, 300.0)
    assert (
        stuck.authz_revocation_oldest_seconds == 660.0 and "authz_revocation_stuck" in caplog.text
    )
    assert await count(db, AuthzOutbox) == 2


async def test_registration_follows_the_mode_and_targets_the_fenced_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
):
    import app.scheduler as scheduler_module
    import app.scheduler_runtime as runtime

    scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    scheduler.start()
    monkeypatch.setattr(scheduler_module, "get_scheduler", lambda: scheduler)
    try:
        for name, value in {"authz_mode": "shadow", **PINNED}.items():
            monkeypatch.setattr(scheduler_module.settings, name, value)
        scheduler_module.register_authz_jobs()
        for job_id in jobs.JOBS:
            job = scheduler.get_job(job_id)
            assert job is not None and job.func is runtime.run_leader_scheduler_job
            assert job.args == (job_id,) and job.max_instances == 1
        monkeypatch.setattr(scheduler_module.settings, "authz_mode", "legacy")
        scheduler_module.register_authz_jobs()
        assert [job for job in scheduler.get_jobs() if job.id in set(jobs.JOBS)] == []
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.parametrize("healthy", [True, False])
async def test_authz_jobs_are_fenced_by_leadership(monkeypatch: pytest.MonkeyPatch, healthy: bool):
    import app.scheduler_runtime as runtime

    body = AsyncMock()
    monkeypatch.setattr(runtime, "scheduler_leader_is_healthy", AsyncMock(return_value=healthy))
    monkeypatch.setattr(runtime, "authz_job_run", body)
    for job_id in jobs.JOBS:
        await runtime.run_leader_scheduler_job(job_id)
    assert body.await_count == (len(jobs.JOBS) if healthy else 0)


async def test_expiry_skips_a_grant_extended_while_waiting_for_its_lock(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    import app.authz.grants as grants_module

    clock = Clock()
    _world, change = await _queued(db, clock, expires=timedelta(hours=1))
    original = grants_module.lock_object

    async def extended_meanwhile(session, resource_type, resource_id):
        await session.execute(
            update(ResourceGrant)
            .where(ResourceGrant.id == change.grant.id)
            .values(expires_at=clock.now + timedelta(days=30))
        )
        await original(session, resource_type, resource_id)

    monkeypatch.setattr(grants_module, "lock_object", extended_meanwhile)
    later = clock.tick(7200)
    assert await jobs.expire_grants(TestSession, FakeTupleStore(), now=later) == []
    grant = await db.get(ResourceGrant, change.grant.id, populate_existing=True)
    assert grant is not None and grant.state == "active" and await count(db, AuthzRevocation) == 0


async def test_expire_leaves_an_unexpired_grant_untouched(db: AsyncSession):
    clock = Clock()
    _world, change = await _queued(db, clock, expires=timedelta(hours=1))
    assert await service(FakeTupleStore(), clock).expire(db, change.grant.id) is None
    assert await count(db, AuthzRevocation) == 0  # no marker, no epoch bump, no org lock
