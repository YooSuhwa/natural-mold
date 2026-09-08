"""Scheduler leadership and cross-process trigger reconciliation contracts."""

from __future__ import annotations

import importlib
import uuid
from unittest.mock import AsyncMock

import pytest
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.model import Model
from app.models.user import User
from app.scheduler_job_ids import LeaderSchedulerJobId
from tests.conftest import TEST_USER_ID, TestSession


@pytest.mark.asyncio
async def test_nonleader_retries_and_activates_scheduler_after_takeover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a process that does not currently hold scheduler leadership.
    runtime = importlib.import_module("app.scheduler_runtime")
    events: list[str] = []

    async def unhealthy() -> bool:
        events.append("health")
        return False

    async def acquired() -> bool:
        events.append("acquire")
        return True

    async def activate() -> None:
        events.append("activate")

    def stop() -> None:
        events.append("stop")

    monkeypatch.setattr(runtime, "scheduler_leader_is_healthy", unhealthy)
    monkeypatch.setattr(runtime, "try_acquire_scheduler_leader", acquired)
    monkeypatch.setattr(runtime, "activate_scheduler", activate)
    monkeypatch.setattr(runtime, "stop_scheduler", stop)

    # When one leadership maintenance tick runs.
    is_leader = await runtime.maintain_scheduler_leadership()

    # Then the stale local scheduler is fenced before the process takes over.
    assert is_leader is True
    assert events == ["health", "stop", "acquire", "activate"]


@pytest.mark.asyncio
async def test_failed_takeover_releases_leadership_for_next_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given leadership is acquired but shared-trigger reconciliation fails.
    runtime = importlib.import_module("app.scheduler_runtime")
    events: list[str] = []
    monkeypatch.setattr(runtime, "scheduler_leader_is_healthy", AsyncMock(return_value=False))
    monkeypatch.setattr(runtime, "try_acquire_scheduler_leader", AsyncMock(return_value=True))
    monkeypatch.setattr(
        runtime,
        "activate_scheduler",
        AsyncMock(side_effect=SQLAlchemyError("temporary database outage")),
    )
    monkeypatch.setattr(runtime, "stop_scheduler", lambda: events.append("stop"))

    async def release() -> None:
        events.append("release")

    monkeypatch.setattr(runtime, "release_scheduler_leader", release)

    # When the new leader cannot finish activation.
    is_leader = await runtime.maintain_scheduler_leadership()

    # Then its paused scheduler and lock are cleared so the next tick can retry.
    assert is_leader is False
    assert events == ["stop", "stop", "release"]


@pytest.mark.asyncio
async def test_scheduled_trigger_is_fenced_immediately_after_leadership_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a due trigger in a process that no longer owns scheduler leadership.
    import app.agent_runtime.trigger_executor as trigger_executor
    import app.scheduler as scheduler

    execute_trigger = AsyncMock()
    monkeypatch.setattr(trigger_executor, "execute_trigger", execute_trigger)
    monkeypatch.setattr(scheduler, "scheduler_leader_is_healthy", AsyncMock(return_value=False))

    # When the stale process attempts to execute its persisted scheduled job.
    await scheduler.execute_scheduled_trigger(str(uuid.uuid4()), "schedule-fingerprint")

    # Then user work is fenced before the trigger executor is entered.
    execute_trigger.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduler_activation_reconciles_persisted_jobs_before_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a newly elected leader with a stopped scheduler.
    runtime = importlib.import_module("app.scheduler_runtime")
    events: list[str] = []

    class Scheduler:
        running = False

        def start(self, *, paused: bool = False) -> None:
            self.running = True
            events.append(f"start:{paused}")

        def resume(self) -> None:
            events.append("resume")

    async def reconcile() -> int:
        events.append("reconcile")
        return 0

    scheduler = Scheduler()
    monkeypatch.setattr(runtime, "get_scheduler", lambda: scheduler)
    monkeypatch.setattr(runtime, "reconcile_trigger_jobs", reconcile)
    for registration_name in (
        "register_credential_rotation_job",
        "register_health_check_job",
        "register_catalog_update_job",
        "register_mcp_health_job",
        "register_broker_eviction_job",
        "register_conversation_run_stale_sweep_job",
        "register_conversation_queue_recovery_job",
        "register_refresh_token_gc_job",
        "register_draft_conversation_gc_job",
        "register_orphan_attachment_gc_job",
        "register_skill_draft_gc_job",
        "cleanup_skill_runtime_roots",
        "register_skill_runtime_cleanup_job",
    ):
        monkeypatch.setattr(runtime, registration_name, lambda: None)

    # When leadership activation rebuilds process-local jobs.
    await runtime.activate_scheduler()

    # Then persisted jobs cannot execute until shared definitions are reconciled.
    assert events == ["start:True", "reconcile", "resume"]


@pytest.mark.asyncio
async def test_leader_owned_maintenance_job_is_fenced_after_lock_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a persisted maintenance job in a process that lost leadership.
    runtime = importlib.import_module("app.scheduler_runtime")
    rotation = AsyncMock()
    monkeypatch.setattr(runtime, "scheduler_leader_is_healthy", AsyncMock(return_value=False))
    monkeypatch.setattr(runtime, "rotate_credentials_to_active_key", rotation)

    # When the old scheduler submits the due job before its next monitor tick.
    await runtime.run_leader_scheduler_job(LeaderSchedulerJobId.CREDENTIAL_ROTATION)

    # Then maintenance work is fenced before touching shared state.
    rotation.assert_not_awaited()


@pytest.mark.asyncio
async def test_maintenance_job_registration_uses_leader_fenced_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a running scheduler owned by this process.
    import app.scheduler as scheduler_module
    import app.scheduler_runtime as runtime

    scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    scheduler.start()
    monkeypatch.setattr(scheduler_module, "get_scheduler", lambda: scheduler)

    try:
        # When a persisted maintenance job is registered.
        scheduler_module.register_broker_eviction_job()

        # Then the durable job targets the leadership-fenced dispatcher.
        job = scheduler.get_job(scheduler_module.BROKER_EVICTION_JOB_ID)
        assert job is not None
        assert job.func is runtime.run_leader_scheduler_job
        assert job.args == (LeaderSchedulerJobId.BROKER_EVICTION,)
    finally:
        scheduler.shutdown(wait=False)


async def _seed_trigger(db: AsyncSession, *, status: str) -> AgentTrigger:
    user = await db.get(User, TEST_USER_ID)
    if user is None:
        user = User(id=TEST_USER_ID, email="scheduler-leader@test.dev", name="Scheduler")
        db.add(user)
    model = Model(provider="openai", model_name=f"scheduler-{uuid.uuid4()}", display_name="Test")
    db.add(model)
    await db.flush()
    agent = Agent(
        user_id=user.id,
        name="Scheduler Agent",
        system_prompt="Test",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()
    trigger = AgentTrigger(
        agent_id=agent.id,
        user_id=user.id,
        name=f"{status} trigger",
        trigger_type="interval",
        schedule_config={"interval_minutes": 10},
        input_message="Test",
        status=status,
    )
    db.add(trigger)
    await db.commit()
    await db.refresh(trigger)
    return trigger


@pytest.mark.asyncio
async def test_leader_reconciles_trigger_changes_written_by_another_process(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given active and paused definitions written to the shared database.
    runtime = importlib.import_module("app.scheduler_runtime")
    active = await _seed_trigger(db, status="active")
    paused = await _seed_trigger(db, status="paused")
    scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    scheduler.start()
    monkeypatch.setattr("app.scheduler.get_scheduler", lambda: scheduler)
    monkeypatch.setattr(runtime, "get_scheduler", lambda: scheduler)
    monkeypatch.setattr(runtime, "async_session", TestSession)
    from app.scheduler import add_trigger_job

    add_trigger_job(paused.id, "interval", {"interval_minutes": 10})

    try:
        # When the leader performs its periodic database reconciliation.
        await runtime.reconcile_trigger_jobs()

        # Then only the active database definition has a scheduled job.
        assert scheduler.get_job(f"trigger_{active.id}") is not None
        assert scheduler.get_job(f"trigger_{paused.id}") is None
        async with TestSession() as verification_db:
            refreshed_active = await verification_db.get(AgentTrigger, active.id)
            refreshed_paused = await verification_db.get(AgentTrigger, paused.id)
            assert refreshed_active is not None
            assert refreshed_active.next_run_at is not None
            assert refreshed_paused is not None
            assert refreshed_paused.next_run_at is None
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_reconciliation_preserves_next_run_for_unchanged_interval(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a leader that has already scheduled an active interval trigger.
    runtime = importlib.import_module("app.scheduler_runtime")
    active = await _seed_trigger(db, status="active")
    scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    scheduler.start()
    monkeypatch.setattr("app.scheduler.get_scheduler", lambda: scheduler)
    monkeypatch.setattr(runtime, "get_scheduler", lambda: scheduler)
    monkeypatch.setattr(runtime, "async_session", TestSession)

    try:
        await runtime.reconcile_trigger_jobs()
        first_job = scheduler.get_job(f"trigger_{active.id}")
        assert first_job is not None
        first_next_run = first_job.next_run_time

        # When the same database definition is reconciled again.
        await runtime.reconcile_trigger_jobs()

        # Then its existing cadence is preserved instead of restarting the interval.
        reconciled_job = scheduler.get_job(f"trigger_{active.id}")
        assert reconciled_job is not None
        assert reconciled_job.next_run_time == first_next_run
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_reconciliation_replaces_job_after_remote_schedule_change(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given an active trigger already scheduled from the shared database.
    runtime = importlib.import_module("app.scheduler_runtime")
    active = await _seed_trigger(db, status="active")
    scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    scheduler.start()
    monkeypatch.setattr("app.scheduler.get_scheduler", lambda: scheduler)
    monkeypatch.setattr(runtime, "get_scheduler", lambda: scheduler)
    monkeypatch.setattr(runtime, "async_session", TestSession)

    try:
        await runtime.reconcile_trigger_jobs()
        active.schedule_config = {"interval_minutes": 20}
        await db.commit()

        # When the leader observes a schedule change written by another process.
        await runtime.reconcile_trigger_jobs()

        # Then the process-local job is replaced with the new cadence.
        job = scheduler.get_job(f"trigger_{active.id}")
        assert job is not None
        assert job.trigger.interval.total_seconds() == 20 * 60
    finally:
        scheduler.shutdown(wait=False)
