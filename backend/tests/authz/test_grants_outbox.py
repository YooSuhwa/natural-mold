"""16.4 outbox: atomicity, immediate apply, retry, INSERT order and failure isolation."""

import logging
from collections.abc import Sequence
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import outbox
from app.authz.fake import FakeTupleStore
from app.authz.grants import GrantRequest, GrantService
from app.authz.outbox import process_outbox
from app.authz.tuples import RelationTuple
from app.models.authz import AuthzOutbox, ResourceGrant
from tests.authz.grant_testkit import Clock, agent_target, count, outbox_rows, seed_world, service
from tests.authz.revocation_testkit import _fresh_pending_hint  # noqa: F401 - autouse
from tests.conftest import TestSession


def viewer(principal) -> GrantRequest:
    return GrantRequest("viewer", "user", principal)


async def test_commit_then_immediate_apply_writes_the_tuple(db: AsyncSession):
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    grants, target = service(store, clock), await agent_target(db, world)
    change = await grants.grant(db, world.ctx, target, viewer(world.member))
    await db.commit()
    run = await grants.apply(change)
    assert run.processed == change.outbox_ids and len(change.outbox_ids) == 1
    expected = RelationTuple(f"user:{world.member}", "viewer", f"agent:{target.resource_id}")
    assert store.stored() == {expected}
    [row] = await outbox_rows(db)
    assert (
        row.processed_at is not None
        and row.tuple_key == f"{expected.user} viewer {expected.object}"
    )


async def test_rollback_leaves_neither_grant_nor_outbox_row(db: AsyncSession):
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    grants, target = service(store, clock), await agent_target(db, world)
    change = await grants.grant(db, world.ctx, target, viewer(world.member))
    await db.rollback()
    assert await count(db, ResourceGrant) == 0 and await count(db, AuthzOutbox) == 0
    assert (await grants.apply(change)).processed == ()
    assert store.stored() == set()


async def test_failed_immediate_apply_stays_queued_and_retries_idempotently(db: AsyncSession):
    world, store, clock = await seed_world(db), FakeTupleStore(fail_calls=1), Clock()
    grants, target = service(store, clock), await agent_target(db, world)
    change = await grants.grant(db, world.ctx, target, viewer(world.member))
    await db.commit()
    assert (await grants.apply(change)).failed == change.outbox_ids
    [row] = await outbox_rows(db)
    assert row.processed_at is None and row.attempts == 1
    assert "AuthzUnavailable" in (row.last_error or "")
    assert (await process_outbox(TestSession, store, now=clock.tick(1))).processed == ()
    assert (await process_outbox(TestSession, store, now=clock.tick(2))).processed == (row.id,)
    await db.refresh(row)
    row.processed_at = None  # a crash after the OpenFGA write but before commit replays it
    await db.commit()
    assert (await process_outbox(TestSession, store, now=clock.tick())).processed == (row.id,)
    assert len(store.stored()) == 1


async def test_rows_apply_in_insert_order_even_when_the_clock_runs_backwards(db: AsyncSession):
    world, store = await seed_world(db), FakeTupleStore()
    late, early = Clock(), Clock()
    late.tick(60)  # the write is stamped a minute after the delete that follows it
    target = await agent_target(db, world)
    change = await service(store, late).grant(db, world.ctx, target, viewer(world.member))
    await db.commit()
    revoke = await service(store, early).revoke(db, world.ctx, change.grant.id)
    await db.commit()
    run = await process_outbox(TestSession, store, now=late.tick(60))
    assert run.processed == (*change.outbox_ids, *revoke.outbox_ids)
    assert store.stored() == set()  # the delete, inserted last, applied last


async def test_partial_batch_failure_only_holds_back_the_failing_rows(db: AsyncSession):
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    grants = service(store, clock)
    good, bad = await agent_target(db, world), await agent_target(db, world)
    for target in (good, bad):
        await grants.grant(db, world.ctx, target, viewer(world.member))
    await db.commit()
    store.failing_objects = {f"agent:{bad.resource_id}"}
    run = await process_outbox(TestSession, store, now=clock.tick())
    assert len(run.processed) == 1 and len(run.failed) == 1
    assert {item.object for item in store.stored()} == {f"agent:{good.resource_id}"}
    store.failing_objects = set()
    assert len((await process_outbox(TestSession, store, now=clock.tick(10))).processed) == 1


class BrokenWriter:
    """A writer bug (not an OpenFGA outage) must still back off instead of escaping."""

    async def write(self, tuples: Sequence[RelationTuple]) -> None:
        raise TypeError("bad payload")

    async def delete(self, tuples: Sequence[RelationTuple]) -> None:
        raise TypeError("bad payload")


async def test_unexpected_writer_errors_back_off_the_row(db: AsyncSession):
    world, clock = await seed_world(db), Clock()
    grants = GrantService(BrokenWriter(), TestSession, clock=clock)
    change = await grants.grant(db, world.ctx, await agent_target(db, world), viewer(world.member))
    await db.commit()
    assert (await grants.apply(change)).failed == change.outbox_ids
    [row] = await outbox_rows(db)
    assert row.attempts == 1 and "TypeError" in (row.last_error or "")


async def test_completion_failure_keeps_the_applied_rows(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    grants = service(store, clock)
    change = await grants.grant(db, world.ctx, await agent_target(db, world), viewer(world.member))
    await db.commit()
    await grants.apply(change)
    revoke = await grants.revoke(db, world.ctx, change.grant.id)
    await db.commit()

    async def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("completion bug")

    monkeypatch.setattr(outbox, "complete_sources", broken)
    assert (await grants.apply(revoke)).processed == revoke.outbox_ids
    assert all(row.processed_at is not None for row in await outbox_rows(db))
    grant = await db.get(ResourceGrant, change.grant.id, populate_existing=True)
    assert grant is not None and grant.state == "revoking"  # the marker keeps denying


async def test_immediate_apply_never_raises(db: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    world, store, clock = await seed_world(db), FakeTupleStore(), Clock()
    grants = service(store, clock)
    change = await grants.grant(db, world.ctx, await agent_target(db, world), viewer(world.member))
    await db.commit()

    async def broken(*args: object, **kwargs: object) -> None:
        raise ValueError("storage bug")

    monkeypatch.setattr(outbox, "process_outbox", broken)
    assert (await grants.apply(change)).processed == ()


def test_failure_alert_fires_at_ten_and_every_ten_after(caplog: pytest.LogCaptureFixture):
    row = AuthzOutbox(attempts=0)
    stamp = Clock().now
    alerts = []
    for _ in range(21):
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger=outbox.__name__):
            outbox._back_off(row, RuntimeError("x"), stamp)  # noqa: SLF001
        if any(record.message == "authz_outbox_failed" for record in caplog.records):
            alerts.append(row.attempts)
    assert alerts == [10, 20]
    assert row.next_attempt_at == stamp + timedelta(minutes=5)  # capped backoff
