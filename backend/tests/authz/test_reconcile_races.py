"""C-07 review: a revoke or grant that lands *during* a run is never undone.

The hook runs right after the run's first snapshot of the partition (the PostgreSQL
``seq``-gap window: committed, applied and finalized before the hold check). ``aged``
moves that activity outside the hold window, so only the fresh re-read can save it.
"""

from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import reconcile
from app.authz.grant_validation import GrantTarget
from app.authz.grants import GrantRequest
from app.authz.reconcile import reconcile_store
from app.authz.tuple_types import RelationTuple
from tests.authz.grant_testkit import START, service
from tests.authz.reconcile_testkit import Fixture, age_activity, seed_fixture
from tests.authz.revocation_testkit import _fresh_pending_hint  # noqa: F401 - autouse
from tests.conftest import TestSession


def _after_first_snapshot(
    monkeypatch: pytest.MonkeyPatch, hook: Callable[[], Awaitable[None]]
) -> None:
    real, calls = reconcile.load_snapshot, []

    async def wrapped(db, **kwargs):
        found = await real(db, **kwargs)
        calls.append(1)
        if len(calls) == 1:
            await hook()
        return found

    monkeypatch.setattr(reconcile, "load_snapshot", wrapped)


async def _bootstrapped(db: AsyncSession) -> Fixture:
    fx = await seed_fixture(db)
    await reconcile_store(TestSession, fx.store, mode="fill", drain=True, now=START)
    await age_activity(db)
    return fx


@pytest.mark.parametrize("aged", [False, True])
async def test_revoke_during_the_run_is_not_rewritten(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, aged: bool
):
    fx = await _bootstrapped(db)
    fx.store.tuples.pop((fx.share.user, fx.share.relation, fx.share.object))  # looks missing

    async def revoke() -> None:
        async with TestSession() as other:
            change = await service(fx.store, fx.clock).revoke(other, fx.world.ctx, fx.share_grant)
            await other.commit()
        assert (await service(fx.store, fx.clock).apply(change)).processed
        if aged:
            await age_activity(db)

    _after_first_snapshot(monkeypatch, revoke)
    report = await reconcile_store(TestSession, fx.store, mode="repair", drain=True, now=START)
    assert report.missing == 0 and report.held == 1 and report.queued == 0
    assert fx.share not in fx.store.stored()


@pytest.mark.parametrize("aged", [False, True])
async def test_grant_applied_during_the_run_is_not_deleted(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, aged: bool
):
    fx = await _bootstrapped(db)
    new = RelationTuple(f"user:{fx.world.member}", "manager", f"agent:{fx.agent}")

    async def grant() -> None:
        async with TestSession() as other:
            grants = service(fx.store, fx.clock)
            target = GrantTarget("agent", fx.agent)
            request = GrantRequest("manager", "user", fx.world.member)
            change = await grants.grant(other, fx.world.ctx, target, request)
            await other.commit()
        await grants.apply(change)
        if aged:
            await age_activity(db)

    _after_first_snapshot(monkeypatch, grant)
    # The store read happens first, so the stored side must already show the new tuple.
    fx.store.tuples[(new.user, new.relation, new.object)] = new
    report = await reconcile_store(TestSession, fx.store, mode="repair", drain=True, now=START)
    assert report.extra == 0 and report.held == 1 and report.queued == 0
    assert new in fx.store.stored()
