"""H-04 recorded reconcile runs (plan 14.3, S27): one live run, progress, outcomes."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import reconcile_launch, reconcile_runs
from app.authz.config import AuthzSettings
from app.authz.reconcile import reconcile_store
from app.authz.reconcile_launch import run_manual
from app.authz.reconcile_runs import claim_run, execute_run, reconcile_status, scheduled_reconcile
from app.models.authz_reconcile_run import AuthzReconcileRun as Run
from tests.authz.grant_testkit import START
from tests.authz.reconcile_testkit import Fixture, age_activity, drift, seed_fixture
from tests.authz.revocation_testkit import _fresh_pending_hint  # noqa: F401 - autouse
from tests.conftest import TestSession


async def _bootstrapped(db: AsyncSession) -> Fixture:
    fx = await seed_fixture(db)
    await reconcile_store(TestSession, fx.store, mode="fill", drain=True, now=START)
    await age_activity(db)
    return fx


async def _claim(db: AsyncSession) -> Run:
    run = await claim_run(db, trigger="manual")
    assert run is not None
    await db.commit()
    return run


async def _row(run_id: object) -> Run:
    async with TestSession() as db:
        row = await db.get(Run, run_id)
        assert row is not None
        return row


async def test_operator_run_repairs_drift_and_records_counts_and_progress(db: AsyncSession):
    fx = await _bootstrapped(db)
    expected = fx.store.stored()
    drift(fx)
    run = await _claim(db)
    report = await execute_run(TestSession, fx.store, run.id, drain=True, now=START)
    assert report is not None and report.diff == 3
    row = await _row(run.id)
    assert (row.status, row.active, row.trigger, row.mode) == (
        "succeeded",
        None,
        "manual",
        "repair",
    )
    assert row.report is not None
    assert (row.report["diff"], row.report["queued"], row.report["repaired"]) == (3, 3, 3)
    assert row.report["deferred"] == 0
    assert set(row.report) >= {"missing", "extra", "changed", "held", "failed"}
    assert row.partitions_total >= 1 and row.partitions_done == row.partitions_total
    assert row.started_at is not None and row.finished_at is not None
    assert fx.store.stored() == expected  # operator runs drain their own rows


async def test_one_live_run_at_a_time_and_the_slot_is_released(db: AsyncSession):
    fx = await _bootstrapped(db)
    run = await _claim(db)
    assert await claim_run(db, trigger="manual") is None
    status = await reconcile_status(db)
    assert status.active is not None and status.active.id == run.id and status.last is None
    await execute_run(TestSession, fx.store, run.id, drain=True, now=START)
    async with TestSession() as other:
        status = await reconcile_status(other)
        assert status.active is None and status.last is not None and status.last.id == run.id
        assert await claim_run(other, trigger="manual") is not None


async def test_failed_run_is_recorded_and_frees_the_slot(db: AsyncSession):
    fx = await _bootstrapped(db)
    drift(fx)
    fx.store.fail_calls = 100  # the store read fails
    run = await _claim(db)
    assert await execute_run(TestSession, fx.store, run.id, drain=True, now=START) is None
    row = await _row(run.id)
    assert (row.status, row.active, row.error) == ("failed", None, "AuthzUnavailable")
    assert row.report is None


async def test_cancelled_run_releases_the_slot(db: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    fx = await _bootstrapped(db)

    async def cancelled(*_args: object, **_kwargs: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(reconcile_runs, "reconcile_store", cancelled)
    run = await _claim(db)
    with pytest.raises(asyncio.CancelledError):
        await execute_run(TestSession, fx.store, run.id, drain=True)
    row = await _row(run.id)
    assert (row.status, row.active, row.error) == ("failed", None, "cancelled")


async def test_abandoned_run_is_closed_before_a_new_claim(db: AsyncSession):
    old = await _claim(db)
    stale = datetime.now(UTC) - reconcile_runs.STALE_AFTER - timedelta(minutes=1)
    await db.execute(update(Run).where(Run.id == old.id).values(updated_at=stale))
    await db.commit()
    fresh = await claim_run(db, trigger="manual")
    assert fresh is not None and fresh.id != old.id
    await db.commit()
    row = await _row(old.id)
    assert (row.status, row.active, row.error) == ("failed", None, "abandoned")


async def test_scheduled_run_is_recorded_and_skips_while_another_is_live(db: AsyncSession):
    fx = await _bootstrapped(db)
    drift(fx)
    calls = len(fx.store.calls)
    report = await scheduled_reconcile(TestSession, fx.store)
    assert report is not None and len(report.outbox_ids) == 3
    assert len(fx.store.calls) == calls  # the daily job leaves its rows to the drain
    rows = list(await db.scalars(select(Run)))
    assert [(r.trigger, r.status) for r in rows] == [("scheduled", "succeeded")]
    assert rows[0].report is not None
    assert (rows[0].report["repaired"], rows[0].report["deferred"]) == (0, 3)
    await _claim(db)
    assert await scheduled_reconcile(TestSession, fx.store) is None


async def test_client_open_failure_fails_the_run(db: AsyncSession):
    run = await _claim(db)

    class Broken:
        async def __aenter__(self) -> None:
            raise OSError("refused")

        async def __aexit__(self, *_exc: object) -> None:
            return None

    await run_manual(run.id, opener=lambda _config: Broken(), sessions=TestSession)  # type: ignore[arg-type,return-value]
    row = await _row(run.id)
    assert (row.status, row.active, row.error) == ("failed", None, "OSError")


async def test_open_failure_after_finish_keeps_the_result(db: AsyncSession):
    run = await _claim(db)
    await reconcile_runs._finish(TestSession, run.id, "succeeded", report={})
    await reconcile_runs.fail_run(TestSession, run.id, "OSError")
    assert (await _row(run.id)).status == "succeeded"
    assert await reconcile_runs.get_run(db, uuid4()) is None


async def test_heartbeat_keeps_a_live_run_from_being_abandoned(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    run = await _claim(db)
    old = datetime.now(UTC) - reconcile_runs.STALE_AFTER - timedelta(minutes=1)
    monkeypatch.setattr(reconcile_runs, "HEARTBEAT", timedelta(milliseconds=10))
    beaten = asyncio.Event()

    async def slow(*_args: object, **_kwargs: object) -> None:
        async with TestSession() as other:  # age the row, then let a beat refresh it
            await other.execute(update(Run).where(Run.id == run.id).values(updated_at=old))
            await other.commit()
        for _ in range(100):
            await asyncio.sleep(0.01)
            if (await _row(run.id)).updated_at.replace(tzinfo=UTC) > old + timedelta(minutes=1):
                beaten.set()
                break
        await reconcile_runs.close_stale(TestSession)  # the leader tick sees a fresh beat
        raise RuntimeError("stop")

    monkeypatch.setattr(reconcile_runs, "reconcile_store", slow)
    await execute_run(TestSession, object(), run.id, drain=True)  # type: ignore[arg-type]
    assert beaten.is_set()
    row = await _row(run.id)
    assert (row.status, row.error) == ("failed", "RuntimeError")  # not "abandoned"


async def test_startup_cleanup_is_a_legacy_noop_and_frees_dead_runs(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    old = await _claim(db)
    stale = datetime.now(UTC) - reconcile_runs.STALE_AFTER - timedelta(minutes=1)
    await db.execute(update(Run).where(Run.id == old.id).values(updated_at=stale))
    await db.commit()
    monkeypatch.setattr(reconcile_launch, "reconcile_sessions", lambda: TestSession)
    await reconcile_launch.close_stale_at_startup(AuthzSettings(authz_mode="legacy"))
    assert (await _row(old.id)).active is True
    pinned = AuthzSettings(authz_mode="shadow", openfga_store_id="s", openfga_model_id="m")
    await reconcile_launch.close_stale_at_startup(pinned)
    assert ((await _row(old.id)).active, (await _row(old.id)).error) == (None, "abandoned")
