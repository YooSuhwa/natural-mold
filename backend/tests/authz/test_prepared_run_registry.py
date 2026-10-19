"""Prepared-run registry: reserve -> pending_commit -> active -> terminalizing -> removed."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import uuid

import pytest

from app.agent_runtime.prepared_run_registry import (
    LeaseState,
    PreparedRunLeaseError,
    PreparedRunRegistry,
)
from tests.authz import prepared_run_testkit as kit


async def _staged(registry: PreparedRunRegistry, probe: kit.CloseProbe | None = None):
    run_binding = kit.binding()
    token = registry.reserve(run_binding)
    lease = kit.lease(run_binding, token, probe)
    await registry.stage(lease)
    return run_binding, token, lease


def _code(exc: pytest.ExceptionInfo[PreparedRunLeaseError]) -> str:
    return exc.value.code


@pytest.mark.asyncio
async def test_reserve_commit_activate_acquire_terminalize_closes_once() -> None:
    registry = PreparedRunRegistry()
    probe = kit.CloseProbe()
    run_binding, token, lease = await _staged(registry, probe)
    run_id = run_binding.run_id
    trace = [registry.state(run_id)]

    registry.activate(run_id, token=token, digest=lease.digest)
    trace.append(registry.state(run_id))
    assert registry.acquire(run_binding, token=token, digest=lease.digest) is lease
    registry.ensure_live_for_marker(run_id, marker_digest=lease.digest)
    outcome = await registry.terminalize(run_id)
    trace.append(registry.state(run_id))

    assert trace == [LeaseState.PENDING_COMMIT, LeaseState.ACTIVE, None]
    assert (outcome, probe.calls) == ("closed", 1)
    assert await registry.terminalize(run_id) == "already_removed"
    assert probe.calls == 1


@pytest.mark.asyncio
async def test_pending_lease_is_inaccessible() -> None:
    registry = PreparedRunRegistry()
    run_binding, token, lease = await _staged(registry)

    with pytest.raises(PreparedRunLeaseError) as acquired:
        registry.acquire(run_binding, token=token, digest=lease.digest)
    with pytest.raises(PreparedRunLeaseError) as marker:
        registry.ensure_live_for_marker(run_binding.run_id, marker_digest=lease.digest)

    assert _code(acquired) == _code(marker) == "prepared_run_lease_missing"


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong", ["token", "digest"])
async def test_activation_and_acquire_require_exact_token_and_digest(wrong: str) -> None:
    registry = PreparedRunRegistry()
    run_binding, token, lease = await _staged(registry)
    run_id = run_binding.run_id
    bad = {"token": token, "digest": lease.digest, wrong: "0" * 64}

    with pytest.raises(PreparedRunLeaseError) as activated:
        registry.activate(run_id, **bad)
    assert _code(activated) == "prepared_run_lease_mismatch"
    assert registry.state(run_id) is LeaseState.PENDING_COMMIT

    registry.activate(run_id, token=token, digest=lease.digest)
    with pytest.raises(PreparedRunLeaseError) as acquired:
        registry.acquire(run_binding, **bad)
    assert _code(acquired) == "prepared_run_lease_mismatch"


@pytest.mark.asyncio
async def test_acquire_requires_exact_binding_and_marker_exact_digest() -> None:
    registry = PreparedRunRegistry()
    run_binding, token, lease = await _staged(registry)
    registry.activate(run_binding.run_id, token=token, digest=lease.digest)
    other_user = dataclasses.replace(run_binding, user_id=uuid.uuid4())

    with pytest.raises(PreparedRunLeaseError) as acquired:
        registry.acquire(other_user, token=token, digest=lease.digest)
    with pytest.raises(PreparedRunLeaseError) as marker:
        registry.ensure_live_for_marker(run_binding.run_id, marker_digest="f" * 64)

    assert _code(acquired) == _code(marker) == "prepared_run_lease_mismatch"


@pytest.mark.asyncio
async def test_rejected_stage_closes_lease_and_keeps_reservation() -> None:
    registry = PreparedRunRegistry()
    run_binding = kit.binding()
    token = registry.reserve(run_binding)
    probes = [kit.CloseProbe(), kit.CloseProbe(), kit.CloseProbe()]

    with pytest.raises(PreparedRunLeaseError) as duplicate:
        registry.reserve(run_binding)
    with pytest.raises(PreparedRunLeaseError) as foreign_token:
        await registry.stage(kit.lease(run_binding, "not-the-token", probes[0]))
    other_binding = kit.binding(uuid.UUID("60000000-0000-4000-8000-000000000077"))
    with pytest.raises(PreparedRunLeaseError) as foreign_binding:
        await registry.stage(kit.lease(other_binding, token, probes[1]))

    assert _code(duplicate) == "prepared_run_lease_duplicate"
    assert _code(foreign_token) == "prepared_run_lease_mismatch"
    assert _code(foreign_binding) == "prepared_run_lease_missing"
    assert registry.state(run_binding.run_id) is LeaseState.RESERVED
    await registry.stage(kit.lease(run_binding, token, probes[2]))
    assert [probe.calls for probe in probes] == [1, 1, 0]


@pytest.mark.asyncio
async def test_terminalize_during_preparation_closes_the_late_lease() -> None:
    registry = PreparedRunRegistry()
    run_binding = kit.binding()
    token = registry.reserve(run_binding)
    assert await registry.terminalize(run_binding.run_id) == "no_lease"
    probe = kit.CloseProbe()

    with pytest.raises(PreparedRunLeaseError) as staged:
        await registry.stage(kit.lease(run_binding, token, probe))

    assert _code(staged) == "prepared_run_lease_missing"
    assert probe.calls == 1
    assert registry.state(run_binding.run_id) is None


@pytest.mark.asyncio
async def test_marker_without_live_lease_fails_closed_without_rebuild() -> None:
    registry = PreparedRunRegistry()
    probe = kit.CloseProbe()
    run_binding, token, lease = await _staged(registry, probe)
    run_id = run_binding.run_id
    # Commit succeeded but the process lost the lease before activation.
    assert await registry.terminalize(run_id) == "closed"

    with pytest.raises(PreparedRunLeaseError) as missing:
        registry.ensure_live_for_marker(run_id, marker_digest=lease.digest)
    with pytest.raises(PreparedRunLeaseError) as never_reserved:
        registry.ensure_live_for_marker(uuid.uuid4(), marker_digest=lease.digest)
    with pytest.raises(PreparedRunLeaseError) as rebuilt:
        registry.reserve(run_binding)

    assert _code(missing) == _code(never_reserved) == "prepared_run_lease_missing"
    assert _code(rebuilt) == "prepared_run_lease_duplicate"
    assert registry.state(run_id) is None
    with pytest.raises(PreparedRunLeaseError):
        registry.activate(run_id, token=token, digest=lease.digest)
    assert probe.calls == 1


@pytest.mark.asyncio
async def test_concurrent_terminalize_closes_exactly_once() -> None:
    registry = PreparedRunRegistry()
    probe = kit.CloseProbe(delay=0.05)
    run_binding, token, lease = await _staged(registry, probe)
    registry.activate(run_binding.run_id, token=token, digest=lease.digest)

    outcomes = await asyncio.gather(
        registry.terminalize(run_binding.run_id), registry.terminalize(run_binding.run_id)
    )

    assert outcomes == ["closed", "closed"]
    assert probe.calls == 1
    assert registry.state(run_binding.run_id) is None


@pytest.mark.asyncio
async def test_cancelled_terminalize_still_closes_and_removes() -> None:
    registry = PreparedRunRegistry()
    probe = kit.CloseProbe(delay=0.05)
    run_binding, token, lease = await _staged(registry, probe)
    run_id = run_binding.run_id
    registry.activate(run_id, token=token, digest=lease.digest)

    caller = asyncio.create_task(registry.terminalize(run_id))
    await probe.started.wait()
    assert registry.state(run_id) is LeaseState.TERMINALIZING
    with pytest.raises(PreparedRunLeaseError):
        registry.acquire(run_binding, token=token, digest=lease.digest)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    assert await registry.terminalize(run_id) == "closed"
    assert probe.calls == 1
    assert registry.state(run_id) is None


class _StubbornClose:
    """A close() that swallows cancellation and keeps running."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> None:
        self.calls += 1
        try:
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            await asyncio.sleep(0.5)


@pytest.mark.asyncio
async def test_cleanup_bound_failure_and_cancelled_close_are_contained(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    registry = PreparedRunRegistry(cleanup_timeout_seconds=0.05)
    stubborn = _StubbornClose()
    run_binding = kit.binding()
    token = registry.reserve(run_binding)
    await registry.stage(dataclasses.replace(kit.lease(run_binding, token), close=stubborn))
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await registry.terminalize(run_binding.run_id) == "close_timeout"
    assert loop.time() - started < 0.4

    failing = kit.CloseProbe(error=RuntimeError(f"bad url https://u:{kit.SECRET}@x"))
    cancelled = kit.CloseProbe(error=asyncio.CancelledError())
    for index, probe in enumerate((failing, cancelled), start=1):
        other = kit.binding(uuid.UUID(int=index))
        other_token = registry.reserve(other)
        await registry.stage(kit.lease(other, other_token, probe))
        assert await registry.terminalize(other.run_id) == "close_failed"
        assert registry.state(other.run_id) is None

    assert (stubborn.calls, failing.calls, cancelled.calls) == (1, 1, 1)
    assert kit.SECRET not in caplog.text


@pytest.mark.asyncio
async def test_reserved_without_lease_terminalizes_without_close() -> None:
    registry = PreparedRunRegistry()
    run_binding = kit.binding()
    registry.reserve(run_binding)

    assert await registry.terminalize(run_binding.run_id) == "no_lease"
    assert registry.state(run_binding.run_id) is None
