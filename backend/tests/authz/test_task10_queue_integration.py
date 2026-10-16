"""Native PostgreSQL queue/worker barriers and restart for the prepared lease (I23).

Claim-first holds its gateway locks in an open transaction while the real
archive endpoint waits, then archive terminalizes the claimed-unprepared run;
archive-first (parked inside its scope locks) blocks the claim, cancels the
pending input and leaves nothing claimable. A paired run whose process died
is terminalized by recovery without any reconstruction even after archive,
and restore never replays a canceled input.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import update

from app.agent_runtime.prepared_run_registry import LEASE_MISSING
from app.exceptions import AppError
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services import conversation_run_queue_service as queue
from app.services import conversation_run_queue_worker, conversation_run_worker
from app.services.tenants import organization_archive_enumeration as enumeration
from tests.authz import prepared_entry_testkit as ekit
from tests.authz import prepared_materialization_testkit as kit
from tests.authz import prepared_queue_testkit as qkit
from tests.authz.task10_runtime_barriers import assert_blocked, finish, park_after

if TYPE_CHECKING:
    from tests.authz.task10_native_fixture import Lane

pytestmark = pytest.mark.integration
pytest_plugins = ["tests.authz.task10_native_fixture"]


async def _queued(lane: Lane, tmp_path: Path) -> tuple[ekit.Harness, UUID]:
    async with lane.sessions() as db:
        harness, input_id = await qkit.queue_harness(db, tmp_path, lane.monkeypatch)
    lane.rebind()
    lane.track(harness.seeded.run_id)
    return harness, input_id


async def _input(lane: Lane, input_id: UUID) -> ConversationRunInput:
    async with lane.sessions() as db:
        row = await db.get(ConversationRunInput, input_id)
    assert row is not None
    return row


async def _claim(lane: Lane, harness: ekit.Harness) -> Any:
    seeded = harness.seeded
    async with lane.sessions() as db:
        try:
            claimed = await queue.claim_next_input(
                db, conversation_id=seeded.request.conversation_id, context=seeded.graph.context
            )
        except AppError as error:
            await db.rollback()
            return error.code
        except HTTPException as error:
            await db.rollback()
            return f"http_{error.status_code}"
        await db.commit()
        return claimed


async def test_native_claim_first_transaction_blocks_archive_then_fails_claimed_run(
    task10_lane: Lane, tmp_path: Path
) -> None:
    harness, input_id = await _queued(task10_lane, tmp_path)
    seeded = harness.seeded
    admin = await task10_lane.admin(seeded.graph.context)
    async with task10_lane.sessions() as claimer:
        claimed = await queue.claim_next_input(
            claimer, conversation_id=seeded.request.conversation_id, context=seeded.graph.context
        )
        assert claimed is not None
        run_id, revision = claimed.run.id, claimed.input.revision
        archive = asyncio.create_task(task10_lane.lifecycle(admin, "archive"))
        await assert_blocked(archive)
        await claimer.commit()
    assert (await finish(archive)).status_code == 200
    task10_lane.track(run_id)

    run, row = await task10_lane.run(run_id), await _input(task10_lane, input_id)
    assert (run.status, run.is_active, run.error_code) == ("failed", False, "organization_archived")
    assert run.completed_at is not None and run.runtime_materialized_at is None
    assert (row.status, row.run_id, row.revision) == ("failed", run_id, revision)
    assert await conversation_run_queue_worker.recover_conversation_queue() == 0
    assert kit.SENDS == [] and harness.leases == [] and harness.resolutions == []


async def test_native_archive_first_blocks_claim_and_cancels_pending_input(
    task10_lane: Lane, tmp_path: Path
) -> None:
    harness, input_id = await _queued(task10_lane, tmp_path)
    admin = await task10_lane.admin(harness.seeded.graph.context)
    before = await _input(task10_lane, input_id)
    gate = park_after(task10_lane.monkeypatch, enumeration, "lock_archive_scope")

    archive = asyncio.create_task(task10_lane.lifecycle(admin, "archive"))
    await gate.wait_entered()
    claim = asyncio.create_task(_claim(task10_lane, harness))
    await assert_blocked(claim)
    gate.release.set()
    response, claimed = await finish(archive), await finish(claim)

    assert response.status_code == 200
    # Whichever refusal the claim path raises, nothing is bound or launched.
    assert claimed in {None, "organization_unavailable", "http_404"}
    row = await _input(task10_lane, input_id)
    assert (row.status, row.run_id, row.revision) == ("canceled", None, before.revision + 1)
    assert await conversation_run_queue_worker.recover_conversation_queue() == 0
    assert kit.SENDS == [] and harness.leases == []


async def test_native_restart_terminalizes_lost_lease_without_reconstruction(
    task10_lane: Lane, tmp_path: Path
) -> None:
    async with task10_lane.sessions() as db:
        harness, input_id, run_id, lost, registry = await qkit.paired_then_lost(
            db, tmp_path, task10_lane.monkeypatch
        )
    task10_lane.rebind()
    task10_lane.track(run_id)
    admin = await task10_lane.admin(harness.seeded.graph.context)
    # Archive leaves the paired (lease-owned) run alone; recovery owns it.
    assert (await task10_lane.lifecycle(admin, "archive")).status_code == 200
    assert (await task10_lane.run(run_id)).status == "running"
    worker = conversation_run_worker.get_run_task_registry().worker_instance_id
    async with task10_lane.sessions() as db:
        statement = update(ConversationRun).where(ConversationRun.id == run_id)
        await db.execute(statement.values(worker_instance_id=worker))
        await db.commit()
    resolutions = len(harness.resolutions)

    await conversation_run_queue_worker.recover_conversation_queue()

    run, row = await task10_lane.run(run_id), await _input(task10_lane, input_id)
    assert (run.status, run.is_active, run.error_code) == ("failed", False, LEASE_MISSING)
    assert row.status == "failed"
    assert len(harness.resolutions) == resolutions and harness.prepare_agent_calls == 0
    assert kit.SENDS == [] and registry.state(run_id) is None
    await lost.terminalize(run_id)


async def test_native_restore_never_replays_canceled_input(
    task10_lane: Lane, tmp_path: Path
) -> None:
    harness, input_id = await _queued(task10_lane, tmp_path)
    seeded = harness.seeded
    admin = await task10_lane.admin(seeded.graph.context)
    archived = await task10_lane.lifecycle(admin, "archive")
    assert archived.status_code == 200
    canceled = await _input(task10_lane, input_id)
    restored = await task10_lane.lifecycle(admin, "restore", version=archived.headers["etag"])
    assert restored.status_code == 200

    assert await conversation_run_queue_worker.recover_conversation_queue() == 0
    after = await _input(task10_lane, input_id)
    assert (after.status, after.run_id, after.revision) == ("canceled", None, canceled.revision)
    async with task10_lane.sessions() as db:
        fresh = await queue.enqueue_input(
            db,
            conversation_id=seeded.request.conversation_id,
            context=seeded.graph.context,
            client_request_id="after-restore",
            source="chat",
            input_payload=qkit.PAYLOAD,
            attachment_ids=[],
            checkpoint_id=None,
            priority=0,
        )
        await db.commit()
    assert fresh.id != input_id and fresh.status == "pending"
    assert kit.SENDS == [] and harness.leases == []
    calls = task10_lane.scheduler_calls
    assert calls["add_trigger_job"] == calls["resume_trigger_job"] == []
