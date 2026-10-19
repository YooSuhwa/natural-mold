"""C-06 review fixes: metrics, worker death, concurrency, timeouts and row hygiene."""

import asyncio
import logging

import pytest

from app.authz.principal import Principal
from app.authz.service import ShadowDecision, ShadowSample, Verdict
from app.authz.shadow_store import DiffKey, DiffTally, diff_key, diff_rows
from tests.authz.authz_service_testkit import ORG
from tests.authz.shadow_testkit import answering, recorder, rows, sample

LOGGER = "app.authz.shadow"


def summaries(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "authz_shadow_summary"]


async def test_summary_is_logged_periodically_and_once_at_stop(caplog) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    rec = recorder(answering(Verdict.ALLOW), summary_seconds=0.01)
    rec.start()
    rec.submit(sample())
    await asyncio.sleep(0.05)
    periodic = len(summaries(caplog))
    await rec.stop()
    assert periodic >= 2
    last = summaries(caplog)[-1]
    assert len(summaries(caplog)) == periodic + 1
    assert (last.__dict__["submitted"], last.__dict__["evaluated"]) == (1, 1)


async def test_counters_accessor_returns_an_independent_snapshot() -> None:
    rec = recorder(answering(Verdict.FORBIDDEN))
    rec.start()
    rec.submit(sample())
    await rec.stop()
    snap = rec.counters()
    snap.mismatches += 5
    snap.mismatch_by_type[(ORG, "agent")] += 5
    assert rec.counters().mismatches == 1
    assert rec.counters().mismatch_by_type[(ORG, "agent")] == 1


async def test_dead_worker_is_logged_and_closes_the_recorder(caplog) -> None:
    async def boom() -> None:
        raise RuntimeError("worker bug")

    rec = recorder(answering(Verdict.ALLOW))
    rec._run = boom  # type: ignore[method-assign]
    rec.start()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert any("recorder closed" in r.getMessage() for r in caplog.records)
    rec.submit(sample())
    assert rec.counters().dropped == 1
    await asyncio.wait_for(rec.stop(), 1)  # no hang on a dead worker, no restart


async def test_decisions_in_a_batch_run_concurrently_up_to_the_limit() -> None:
    active, peak, gate = 0, 0, asyncio.Event()

    async def slow(_s: ShadowSample) -> ShadowDecision:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await gate.wait()
        active -= 1
        return ShadowDecision(Verdict.ALLOW)

    rec = recorder(slow, concurrency=3, batch_size=20)
    for _ in range(10):
        rec.submit(sample())
    rec.start()
    for _ in range(5):
        await asyncio.sleep(0)
    assert peak == 3
    gate.set()
    await rec.stop()
    assert rec.counters().evaluated == 10


async def test_a_decision_over_its_timeout_counts_as_an_error() -> None:
    async def hang(_s: ShadowSample) -> ShadowDecision:
        await asyncio.Event().wait()
        raise AssertionError

    rec = recorder(hang, decision_seconds=0.01)
    rec.start()
    rec.submit(sample())
    await rec.stop()
    assert (rec.counters().errors, rec.counters().evaluated) == (1, 0)


async def test_oversized_values_are_truncated_to_column_widths() -> None:
    long_obj = "agent:" + "x" * 200
    s = ShadowSample(
        Principal(kind="user", id=sample().principal.id, org_id=ORG),
        "p" * 120,
        long_obj,
        "view",
        ORG,
        Verdict.ALLOW,
    )
    key = diff_key(s, Verdict.FORBIDDEN, "/r" * 200)
    assert key is not None
    assert (len(key.route), len(key.object), len(key.permission)) == (255, 100, 80)
    [row] = diff_rows({key: DiffTally(1, "q" * 300)})
    assert len(row["request_id"]) == 100
    rec = recorder(answering(Verdict.FORBIDDEN))
    rec.start()
    rec.submit(s)
    await rec.stop()
    [stored] = await rows()
    assert (len(stored.object), len(stored.permission)) == (100, 80)


def test_rows_are_sorted_by_key() -> None:
    def key(obj: str) -> DiffKey:
        return DiffKey("/r", "user:u", obj, "edit", True, False)

    tallies = {key("agent:c"): DiffTally(1, None), key("agent:a"): DiffTally(1, None)}
    tallies[key("agent:b")] = DiffTally(1, None)
    assert [r["object"] for r in diff_rows(tallies)] == ["agent:a", "agent:b", "agent:c"]
