"""Restart recovery of paired prepared runs (internal I19).

A run with the durable materialization pair but no live lease terminalizes
without any rebuild (no resolution, hydration or provider send): ``running``
fails ``prepared_run_lease_missing``, ``canceling`` becomes ``canceled``. A
live local lease and a remote owner with a fresh heartbeat are left alone; a
denied gateway (suspended tenant, revoked owner membership) still terminalizes.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.prepared_run_registry import (
    LEASE_MISSING,
    get_prepared_run_registry,
    reset_prepared_run_registry_for_tests,
)
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.conversation_run_input import ConversationRunInput
from app.models.organization import OrganizationMember, Tenant, TenantMember
from app.services import (
    agent_execution_runtime,
    conversation_run_queue_worker,
    conversation_run_worker,
)
from app.services.agent_execution_runtime import RuntimeOwnershipRequest
from app.services.conversation_run_prepared_recovery import terminalize_orphaned_prepared_runs
from tests.authz import prepared_adapter_testkit as akit
from tests.authz import prepared_materialization_testkit as kit
from tests.authz import prepared_queue_testkit as qkit
from tests.conftest import TestSession

STALE = timedelta(days=1)


async def _set(db: AsyncSession, run_id: Any, **values: Any) -> None:
    await db.execute(update(ConversationRun).where(ConversationRun.id == run_id).values(**values))
    await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["stale_remote", "this_worker"])
async def test_restart_with_paired_run_and_no_live_lease_terminalizes(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: str
) -> None:
    harness, input_id, run_id, lost, registry = await qkit.paired_then_lost(
        db, tmp_path, monkeypatch
    )
    worker_id = conversation_run_worker.get_run_task_registry().worker_instance_id
    if owner == "stale_remote":
        await _set(db, run_id, heartbeat_at=utc_now_naive() - STALE)
    else:
        await _set(db, run_id, worker_instance_id=worker_id)
    resolutions = len(harness.resolutions)

    await conversation_run_queue_worker.recover_conversation_queue()

    db.expire_all()
    row = await db.get(ConversationRun, run_id)
    queued = await db.get(ConversationRunInput, input_id)
    assert row is not None and queued is not None
    assert (row.status, row.is_active, row.error_code) == ("failed", False, LEASE_MISSING)
    assert queued.status == "failed"
    assert len(harness.resolutions) == resolutions and kit.SENDS == []
    assert registry.state(run_id) is None and harness.prepare_agent_calls == 0
    await lost.terminalize(run_id)  # test hygiene: close the lost process's lease


@pytest.mark.asyncio
async def test_paired_canceling_orphan_becomes_acknowledged_canceled(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _harness, _input_id, run_id, lost, _registry = await qkit.paired_then_lost(
        db, tmp_path, monkeypatch
    )
    await _set(
        db,
        run_id,
        status="canceling",
        cancel_requested_at=utc_now_naive(),
        heartbeat_at=utc_now_naive() - STALE,
    )

    assert await terminalize_orphaned_prepared_runs(
        session_factory=TestSession, stale_before=utc_now_naive() - timedelta(hours=1)
    ) == [run_id]

    db.expire_all()
    row = await db.get(ConversationRun, run_id)
    assert row is not None and (row.status, row.is_active) == ("canceled", False)
    assert row.cancellation_acknowledged_at is not None
    await lost.terminalize(run_id)


@pytest.mark.asyncio
async def test_live_remote_owner_with_fresh_heartbeat_is_skipped(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _harness, _input_id, run_id, lost, _registry = await qkit.paired_then_lost(
        db, tmp_path, monkeypatch
    )
    await _set(db, run_id, worker_instance_id="remote-worker", heartbeat_at=utc_now_naive())

    assert (
        await terminalize_orphaned_prepared_runs(
            session_factory=TestSession, stale_before=utc_now_naive() - timedelta(hours=1)
        )
        == []
    )

    db.expire_all()
    row = await db.get(ConversationRun, run_id)
    assert row is not None and row.status == "running"
    await lost.terminalize(run_id)


@pytest.mark.asyncio
async def test_live_local_lease_is_never_terminalized_by_recovery(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, _input_id = await qkit.queue_harness(db, tmp_path, monkeypatch)
    run_id = await qkit.claim(db, harness)
    request = harness.seeded.request
    await agent_execution_runtime.materialize_prepared_execution(
        RuntimeOwnershipRequest(
            run_id, request.conversation_id, request.root_agent_id, request.context
        ),
        prepare_inputs=akit.resolver(harness.seeded, tmp_path),
        schedule=_noop_schedule,
        seams=kit.seams(harness.factory, harness.server),
    )
    await _set(db, run_id, heartbeat_at=utc_now_naive() - STALE)

    assert (
        await terminalize_orphaned_prepared_runs(
            session_factory=TestSession, stale_before=utc_now_naive()
        )
        == []
    )

    db.expire_all()
    row = await db.get(ConversationRun, run_id)
    assert row is not None and row.status == "running"
    await get_prepared_run_registry().terminalize(run_id)


async def _noop_schedule(_lease: Any) -> None:
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["tenant_suspended", "tenant_member", "org_member"])
@pytest.mark.parametrize("live", [False, True])
async def test_gateway_denied_orphan_terminalizes_but_live_lease_is_kept(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, denial: str, live: bool
) -> None:
    harness, input_id, run_id, lost, _registry = await qkit.paired_then_lost(
        db, tmp_path, monkeypatch
    )
    if live:
        reset_prepared_run_registry_for_tests(lost)  # the lease is still live here
    ctx = harness.seeded.graph.context
    if denial == "tenant_suspended":
        statement = update(Tenant).where(Tenant.id == ctx.tenant_id)
    elif denial == "tenant_member":
        statement = update(TenantMember).where(TenantMember.user_id == ctx.user_id)
    else:
        statement = update(OrganizationMember).where(OrganizationMember.user_id == ctx.user_id)
    await db.execute(statement.values(status="suspended"))
    await _set(db, run_id, heartbeat_at=utc_now_naive() - STALE)
    resolutions = len(harness.resolutions)

    failed = await terminalize_orphaned_prepared_runs(
        session_factory=TestSession, stale_before=utc_now_naive() - timedelta(hours=1)
    )

    db.expire_all()
    row = await db.get(ConversationRun, run_id)
    queued = await db.get(ConversationRunInput, input_id)
    assert row is not None and queued is not None
    if live:
        assert failed == [] and (row.status, queued.status) == ("running", "claimed")
    else:
        assert failed == [run_id]
        assert (row.status, row.is_active, row.error_code) == ("failed", False, LEASE_MISSING)
        assert queued.status == "failed"
    assert len(harness.resolutions) == resolutions and kit.SENDS == []
    await lost.terminalize(run_id)
