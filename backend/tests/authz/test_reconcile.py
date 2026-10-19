"""C-07: bootstrap fill, rebuild and reconcile against the previous (C-04) fixture."""

from datetime import timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.client import AuthzUnavailable
from app.authz.outbox import TupleOp, enqueue, process_outbox
from app.authz.reconcile import SOURCE_TABLE, reconcile_store, run_reconcile_job
from app.authz.revocations import record_revocation
from app.authz.tuple_snapshot import load_snapshot
from app.authz.tuple_types import RelationTuple
from app.authz.tuples import expected_tuples
from app.models.authz import AuthzOutbox
from tests.authz.grant_testkit import START, count, outbox_rows
from tests.authz.reconcile_testkit import Fixture, age_activity, drift, seed_fixture
from tests.authz.revocation_testkit import _fresh_pending_hint  # noqa: F401 - autouse
from tests.conftest import TestSession


async def _bootstrapped(db: AsyncSession) -> Fixture:
    fx = await seed_fixture(db)
    report = await reconcile_store(TestSession, fx.store, mode="fill", drain=True, now=START)
    assert report.extra == 0 and report.changed == 0  # the outbox projected exactly 7.2
    assert report.queued == report.missing > 0 and report.failed == 0
    await age_activity(db)
    return fx


async def test_bootstrap_loads_every_expected_tuple_and_reconcile_reports_zero(db: AsyncSession):
    fx = await _bootstrapped(db)
    snapshot = await load_snapshot(db)
    assert fx.store.stored() == set(expected_tuples(snapshot, now=START))
    relations = {t.relation for t in fx.store.stored()}
    assert {"member", "admin", "org", "owner", "resident_agent", "server", "tenant"} <= relations
    assert {"cap_create_agent", "viewer", "consumer"} <= relations
    report = await reconcile_store(TestSession, fx.store, now=START)
    assert (report.diff, report.held, report.queued, report.mode) == (0, 0, 0, "dry_run")
    assert report.expected == report.stored == len(fx.store.stored())


async def test_bootstrap_is_idempotent(db: AsyncSession):
    fx = await _bootstrapped(db)
    before, calls = fx.store.stored(), len(fx.store.calls)
    again = await reconcile_store(TestSession, fx.store, mode="fill", drain=True, now=START)
    assert (again.queued, again.missing) == (0, 0)
    assert fx.store.stored() == before and len(fx.store.calls) == calls


async def test_dry_run_reports_drift_without_writing(db: AsyncSession):
    fx = await _bootstrapped(db)
    stray = drift(fx)
    before, calls, rows = fx.store.stored(), len(fx.store.calls), await count(db, AuthzOutbox)
    report = await reconcile_store(TestSession, fx.store, now=START)
    assert (report.missing, report.extra, report.changed, report.queued) == (1, 1, 1, 0)
    assert report.sample_missing == [f"{fx.share.user} viewer {fx.share.object}"]
    assert report.sample_extra == [f"{stray.user} viewer {stray.object}"]
    assert report.sample_changed[0].startswith(f"{fx.expiring.user} consumer")
    assert fx.store.stored() == before and len(fx.store.calls) == calls
    assert await count(db, AuthzOutbox) == rows


async def test_repair_with_drain_fixes_extra_missing_and_condition_drift(db: AsyncSession):
    fx = await _bootstrapped(db)
    expected = fx.store.stored()
    drift(fx)
    report = await reconcile_store(TestSession, fx.store, mode="repair", drain=True, now=START)
    assert (report.diff, report.queued, report.failed) == (3, 3, 0)
    assert fx.store.stored() == expected
    await age_activity(db)
    assert (await reconcile_store(TestSession, fx.store, now=START)).diff == 0


async def test_daily_job_queues_every_repair_in_the_outbox(db: AsyncSession):
    fx = await _bootstrapped(db)
    expected = fx.store.stored()
    drift(fx)
    calls = len(fx.store.calls)
    report = await run_reconcile_job(TestSession, fx.store, now=START)
    assert report.mode == "repair" and len(report.outbox_ids) == 3
    assert len(fx.store.calls) == calls  # nothing written outside the outbox
    rows = [r for r in await outbox_rows(db) if r.id in set(report.outbox_ids)]
    assert {r.source_table for r in rows} == {SOURCE_TABLE}
    assert [r.op for r in rows] == ["delete", "write", "write"]  # extra, changed, missing
    run = await process_outbox(TestSession, fx.store, ids=report.outbox_ids, now=START)
    assert set(run.processed) == set(report.outbox_ids)
    assert fx.store.stored() == expected


@pytest.mark.parametrize("finalized", [False, True])
async def test_marker_pending_or_finalized_during_the_run_blocks_the_rewrite(
    db: AsyncSession, finalized: bool
):
    fx = await _bootstrapped(db)
    fx.store.tuples.pop((fx.share.user, fx.share.relation, fx.share.object))
    world = fx.world
    marker = await record_revocation(
        db,
        org_id=world.ctx.org_id,
        tenant_id=world.tenant,
        scope="grant",
        subject=fx.share.user,
        object=fx.share.object,
    )
    marker.finalized_at = START if finalized else None
    await db.commit()
    report = await reconcile_store(TestSession, fx.store, mode="repair", drain=True, now=START)
    assert (report.missing, report.held, report.queued) == (0, 1, 0)
    assert report.sample_held == [f"{fx.share.user} viewer {fx.share.object}"]
    assert fx.share not in fx.store.stored()
    await age_activity(db)  # a marker finalized long before the run no longer blocks
    later = await reconcile_store(TestSession, fx.store, now=START + timedelta(days=1))
    assert later.held == (0 if finalized else 1)


async def test_in_flight_outbox_key_is_neither_deleted_nor_rewritten(db: AsyncSession):
    fx = await _bootstrapped(db)
    stray = drift(fx)
    for item in (stray, fx.share):
        op = [TupleOp("write", item)]
        await enqueue(db, op, source_table="t", source_id=fx.agent, now=START)
    await db.commit()
    report = await reconcile_store(TestSession, fx.store, mode="repair", drain=True, now=START)
    assert (report.extra, report.missing, report.changed, report.held) == (0, 0, 1, 2)
    assert stray in fx.store.stored() and fx.share not in fx.store.stored()


async def test_scoped_runs_see_only_their_company_or_organization(db: AsyncSession):
    fx = await _bootstrapped(db)
    other = await seed_fixture(db)  # second company, its store is separate
    fx.store.tuples.update(other.store.tuples)
    tenant = other.world.tenant
    fill = await reconcile_store(
        TestSession, fx.store, mode="fill", drain=True, tenant_id=tenant, now=START
    )
    assert fill.scope == f"tenant:{tenant}" and fill.queued > 0
    await age_activity(db)
    drift(fx)

    async def diff(**scope) -> tuple[int, int]:
        report = await reconcile_store(TestSession, fx.store, now=START, **scope)
        return report.diff, report.expected

    (mine, mine_expected), (theirs, _) = (
        await diff(tenant_id=fx.world.tenant),
        await diff(tenant_id=tenant),
    )
    assert (mine, theirs) == (3, 0)
    org, org_expected = await diff(org_id=fx.world.ctx.org_id)
    assert (org, (await diff(org_id=fx.world.other_ctx.org_id))[0]) == (3, 0)
    assert org_expected < mine_expected  # tenant-level tuples are outside an org scope
    assert (await diff())[0] == 3  # store-wide: every company, then the platform partition
    with pytest.raises(ValueError, match="not found"):
        await diff(tenant_id=tenant, org_id=fx.world.ctx.org_id)


async def test_store_wide_run_deletes_tuples_of_rows_missing_from_postgres(db: AsyncSession):
    fx = await _bootstrapped(db)
    orphan = RelationTuple("user:00000000-0000-0000-0000-000000000009", "viewer", "agent:gone")
    fx.store.tuples[(orphan.user, orphan.relation, orphan.object)] = orphan
    scoped = await reconcile_store(TestSession, fx.store, tenant_id=fx.world.tenant, now=START)
    assert scoped.diff == 0
    report = await reconcile_store(TestSession, fx.store, mode="repair", drain=True, now=START)
    assert report.extra == 1 and orphan not in fx.store.stored()


async def test_store_read_failure_aborts_without_writes(db: AsyncSession):
    fx = await _bootstrapped(db)
    drift(fx)
    before, calls = fx.store.stored(), len(fx.store.calls)
    fx.store.fail_calls = 1
    with pytest.raises(AuthzUnavailable):
        await reconcile_store(TestSession, fx.store, mode="repair", drain=True, now=START)
    assert fx.store.stored() == before and len(fx.store.calls) == calls


@pytest.mark.parametrize("column", ["processed_at", "created_at"])
async def test_activity_since_the_hold_window_started_holds_the_key(db: AsyncSession, column: str):
    fx = await _bootstrapped(db)
    drift(fx)
    recent = {column: START - timedelta(seconds=30)}  # inside the 60 s margin
    await db.execute(update(AuthzOutbox).values(**recent))
    await db.commit()
    report = await reconcile_store(TestSession, fx.store, now=START)
    assert report.held == 2 and report.missing == 0  # share + expiring keys had grant rows
