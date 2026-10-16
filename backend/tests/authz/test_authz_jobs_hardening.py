"""C-05 review hardening: idle drain, expiry paging past failures, prune chunks, repair lock."""

from datetime import timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import jobs
from app.authz.fake import FakeTupleStore
from app.models.authz import AuthzOutbox, ResourceGrant
from app.scheduler_job_ids import LeaderSchedulerJobId as JobId
from tests.authz.grant_testkit import Clock, count
from tests.authz.revocation_testkit import _fresh_pending_hint  # noqa: F401 - autouse
from tests.authz.test_authz_jobs import _queued, config
from tests.conftest import TestSession


async def test_idle_drain_tick_never_opens_openfga(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("app.database.async_session", TestSession)
    clock = Clock()
    await _queued(db, clock)  # due at 09:00 of the fixture day, tested before it
    opened: list[str] = []

    def opener():
        opened.append("openfga")
        raise AssertionError("no due rows: the client must not be opened")

    assert not await jobs.has_due_rows(TestSession, now=clock.now - timedelta(seconds=1))
    assert await jobs.has_due_rows(TestSession, now=clock.now)
    await db.execute(update(AuthzOutbox).values(next_attempt_at=clock.now + timedelta(days=3650)))
    await db.commit()
    await jobs.run_job(JobId.AUTHZ_OUTBOX_DRAIN, config(), opener=opener)
    assert opened == []


async def test_expiry_moves_past_a_failing_grant(db: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    import app.authz.grants as grants_module

    clock = Clock()
    _a, first = await _queued(db, clock, expires=timedelta(hours=1))
    _b, second = await _queued(db, clock, expires=timedelta(hours=2))
    await jobs.drain_outbox(TestSession, FakeTupleStore(), now=clock.now)
    original = grants_module.GrantService.expire
    tried: list[object] = []

    async def failing(self, session, grant_id):
        tried.append(grant_id)
        if grant_id == first.grant.id:
            raise RuntimeError("bad row")
        return await original(self, session, grant_id)

    monkeypatch.setattr(grants_module.GrantService, "expire", failing)
    later = clock.tick(3 * 3600)
    closed = await jobs.expire_grants(TestSession, FakeTupleStore(), now=later, limit=1)
    assert closed == [second.grant.id] and tried == [first.grant.id, second.grant.id]
    kept = await db.get(ResourceGrant, first.grant.id, populate_existing=True)
    assert kept is not None and kept.state == "active"


async def test_prune_works_in_bounded_chunks(db: AsyncSession):
    clock = Clock()
    await _queued(db, clock)
    await _queued(db, clock)
    await jobs.drain_outbox(TestSession, FakeTupleStore(), now=clock.now)
    later = clock.now + timedelta(days=8)
    assert await jobs.prune(TestSession, now=later, chunk=1) == (1, 0)
    assert await jobs.prune(TestSession, now=later, chunk=1) == (1, 0)
    assert await count(db, AuthzOutbox) == 0


async def test_repair_run_skips_when_another_holds_the_lock(monkeypatch: pytest.MonkeyPatch):
    from app.authz import reconcile

    async def held(_db):
        return False

    monkeypatch.setattr(reconcile, "_try_repair_lock", held)
    store = FakeTupleStore(fail_calls=1)  # any read would raise
    report = await reconcile.reconcile_store(TestSession, store, mode="repair")
    assert report.skipped and report.queued == 0 and store.fail_calls == 1
    dry = await reconcile.reconcile_store(TestSession, FakeTupleStore(), mode="dry_run")
    assert not dry.skipped
