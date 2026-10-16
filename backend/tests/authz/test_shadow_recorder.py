"""C-06 shadow recorder: bounded, non-blocking hook and the background worker."""

import asyncio
import inspect

import pytest

from app.authz.config import AuthzSettings
from app.authz.service import ShadowDecision, ShadowSample, Verdict
from app.authz.service_types import AuthzMode
from app.authz.shadow import (
    RateLimit,
    ShadowRecorder,
    bind_shadow_request,
    start_shadow_recorder,
    stop_shadow_recorder,
)
from tests.authz.authz_service_testkit import AGENT, ORG, RecordingClient, grants, service, user
from tests.authz.shadow_testkit import answering, recorder, rows, sample
from tests.conftest import TestSession


def test_submit_is_synchronous_and_never_evaluates_on_the_request_path() -> None:
    calls: list = []
    rec = recorder(answering(Verdict.ALLOW, calls=calls))
    assert not inspect.iscoroutinefunction(rec.submit)
    assert rec.submit(sample()) is None
    assert calls == []


async def test_full_queue_drops_and_counts_without_blocking() -> None:
    rec = recorder(answering(Verdict.ALLOW), queue_size=2)
    for _ in range(5):
        rec.submit(sample())
    assert (rec.counters().submitted, rec.counters().dropped) == (5, 3)


def test_rate_limit_is_a_per_second_token_bucket() -> None:
    now = [0.0]
    limit = RateLimit(2, clock=lambda: now[0])
    assert [limit.admit() for _ in range(3)] == [True, True, False]
    now[0] = 0.5
    assert [limit.admit(), limit.admit()] == [True, False]
    assert RateLimit(0, clock=lambda: now[0]).admit() is False


async def test_samples_over_the_rate_are_counted_not_queued() -> None:
    rec = recorder(answering(Verdict.ALLOW), max_rps=2, clock=lambda: 0.0)
    for _ in range(4):
        rec.submit(sample())
    assert (rec.counters().rate_limited, rec.counters().dropped) == (2, 0)


async def submit_all(verdict: Verdict, *samples: ShadowSample, request_id: str) -> ShadowRecorder:
    rec = recorder(answering(verdict))
    rec.start()
    token = bind_shadow_request("/api/agents/{agent_id}", request_id)
    try:
        for s in samples:
            rec.submit(s)
    finally:
        token.var.reset(token)
    await rec.stop()
    return rec


async def test_worker_aggregates_disagreements_on_the_unique_key() -> None:
    first = await submit_all(Verdict.FORBIDDEN, sample(), sample(), request_id="req-1")
    await submit_all(Verdict.FORBIDDEN, sample(), request_id="req-2")  # upsert increments
    [row] = await rows()
    assert (row.route, row.principal, row.object, row.permission) == (
        "/api/agents/{agent_id}",
        user().fga(),
        AGENT,
        "edit",
    )
    assert (row.legacy, row.fga, row.count, row.request_id) == (True, False, 3, "req-2")
    assert first.counters().mismatches == 2
    assert first.counters().mismatch_by_type[(ORG, "agent")] == 2


async def test_agreement_writes_nothing() -> None:
    rec = recorder(answering(Verdict.ALLOW))
    rec.start()
    rec.submit(sample())
    await rec.stop()
    assert await rows() == []
    assert (rec.counters().evaluated, rec.counters().matched) == (1, 1)


async def test_404_versus_403_is_recorded_on_the_visibility_relation() -> None:
    rec = recorder(answering(Verdict.FORBIDDEN))
    rec.start()
    rec.submit(sample(Verdict.NOT_FOUND))
    await rec.stop()
    [row] = await rows()
    assert (row.permission, row.legacy, row.fga) == ("view", False, True)


async def test_overlay_vetoed_samples_are_excluded_from_diffs() -> None:
    rec = recorder(answering(Verdict.FORBIDDEN, vetoed=True))
    rec.start()
    rec.submit(sample())
    await rec.stop()
    assert await rows() == []
    assert (rec.counters().overlay_vetoed, rec.counters().mismatches) == (1, 0)


async def test_end_to_end_through_the_service_hook() -> None:
    client = RecordingClient(allowed=grants("view"))
    holder: list[ShadowRecorder] = []
    svc = service(client, "shadow", hook=lambda s: holder[0].submit(s))
    holder.append(recorder(svc.shadow_decide))
    holder[0].start()
    await svc.require(
        user(), "edit", AGENT, min_visible="view", resource_org_id=ORG, legacy=Verdict.ALLOW
    )
    assert client.queries == []  # OpenFGA was not consulted on the request path
    await holder[0].stop()
    [row] = await rows()
    assert (row.permission, row.legacy, row.fga) == ("edit", True, False)


async def test_openfga_outage_is_counted_and_the_worker_survives() -> None:
    svc = service(RecordingClient(unavailable=True), "shadow")
    rec = recorder(svc.shadow_decide)
    rec.start()
    rec.submit(sample())
    rec.submit(sample())
    await rec.stop()
    assert (rec.counters().errors, rec.counters().evaluated) == (2, 0)
    assert await rows() == []


async def test_write_failure_is_counted_and_the_worker_survives() -> None:
    def broken():
        raise RuntimeError("db down")

    calls: list = []
    rec = ShadowRecorder(
        answering(Verdict.FORBIDDEN, calls=calls), max_rps=1000, sessions=broken, batch_size=1
    )
    rec.start()
    rec.submit(sample())
    rec.submit(sample())
    await rec.stop()
    assert (rec.counters().write_errors, len(calls)) == (2, 2)


async def test_stop_drains_queued_samples() -> None:
    calls: list = []
    rec = recorder(answering(Verdict.ALLOW, calls=calls), batch_size=3)
    rec.start()
    for _ in range(10):
        rec.submit(sample())
    await rec.stop()
    assert len(calls) == 10
    rec.submit(sample())
    assert rec.counters().dropped == 1  # closed after stop


async def test_stop_gives_up_after_the_timeout_and_counts_the_rest() -> None:
    gate = asyncio.Event()

    async def stuck(_s: ShadowSample) -> ShadowDecision:
        await gate.wait()
        return ShadowDecision(Verdict.ALLOW)

    rec = recorder(stuck, batch_size=1)
    rec.start()
    for _ in range(3):
        rec.submit(sample())
    await asyncio.sleep(0)
    await asyncio.wait_for(rec.stop(drain_seconds=0.05), 1)
    assert rec.counters().dropped == 3  # one in flight was cancelled, two never started


@pytest.mark.parametrize("mode", ["legacy", "enforce"])
async def test_lifespan_start_is_a_noop_outside_shadow_mode(mode: AuthzMode) -> None:
    settings = AuthzSettings(authz_mode=mode)
    assert start_shadow_recorder(settings, answering(Verdict.ALLOW)) is None
    await stop_shadow_recorder()


async def test_lifespan_start_and_stop_in_shadow_mode() -> None:
    settings = AuthzSettings(authz_mode="shadow", authz_shadow_max_rps=7)
    rec = start_shadow_recorder(settings, answering(Verdict.ALLOW), sessions=TestSession)
    assert rec is not None
    assert start_shadow_recorder(settings, answering(Verdict.ALLOW)) is rec
    rec.submit(sample())
    await stop_shadow_recorder()
    assert rec.counters().evaluated == 1
    await stop_shadow_recorder()  # idempotent
