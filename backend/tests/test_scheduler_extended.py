"""Extended tests for app.scheduler — job management with MemoryJobStore."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run import utc_now_naive as run_utc_now_naive
from app.models.model import Model
from app.models.user import User
from app.scheduler import (
    BROKER_EVICTION_JOB_ID,
    CATALOG_BOOTSTRAP_JOB_ID,
    CATALOG_UPDATE_JOB_ID,
    CONVERSATION_RUN_STALE_SWEEP_JOB_ID,
    add_trigger_job,
    evict_expired_brokers,
    pause_trigger_job,
    register_broker_eviction_job,
    register_catalog_update_job,
    register_conversation_run_stale_sweep_job,
    remove_trigger_job,
    resume_trigger_job,
    sweep_stale_conversation_runs,
)
from app.services import conversation_run_service
from app.services.conversation_run_worker import (
    RunTaskRegistry,
    reset_run_task_registry_for_tests,
)
from tests.conftest import TEST_USER_ID, TestSession


@pytest.fixture
async def running_scheduler():  # type: ignore[misc]
    """Create a scheduler with MemoryJobStore that is running (async context)."""
    sched = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    sched.start(paused=True)  # needs an event loop; async fixture provides one
    yield sched
    sched.shutdown(wait=False)


@pytest.fixture(autouse=True)
async def _patch_scheduler(running_scheduler: AsyncIOScheduler):
    """Patch get_scheduler to return our test scheduler."""
    with patch("app.scheduler.get_scheduler", return_value=running_scheduler):
        yield


# ---------------------------------------------------------------------------
# add_trigger_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_interval_trigger(running_scheduler: AsyncIOScheduler):
    trigger_id = uuid.uuid4()
    add_trigger_job(trigger_id, "interval", {"interval_minutes": 5})

    job = running_scheduler.get_job(f"trigger_{trigger_id}")
    assert job is not None
    assert job.trigger.interval.total_seconds() == 5 * 60


@pytest.mark.asyncio
async def test_add_cron_trigger(running_scheduler: AsyncIOScheduler):
    trigger_id = uuid.uuid4()
    add_trigger_job(trigger_id, "cron", {"cron_expression": "30 9 * * *"})

    job = running_scheduler.get_job(f"trigger_{trigger_id}")
    assert job is not None


@pytest.mark.asyncio
async def test_add_interval_default_minutes(running_scheduler: AsyncIOScheduler):
    """Default interval_minutes is 10 when not specified."""
    trigger_id = uuid.uuid4()
    add_trigger_job(trigger_id, "interval", {})

    job = running_scheduler.get_job(f"trigger_{trigger_id}")
    assert job is not None
    assert job.trigger.interval.total_seconds() == 10 * 60


@pytest.mark.asyncio
async def test_add_trigger_replaces_existing(running_scheduler: AsyncIOScheduler):
    trigger_id = uuid.uuid4()
    add_trigger_job(trigger_id, "interval", {"interval_minutes": 5})
    add_trigger_job(trigger_id, "interval", {"interval_minutes": 15})

    job = running_scheduler.get_job(f"trigger_{trigger_id}")
    assert job is not None
    assert job.trigger.interval.total_seconds() == 15 * 60


# ---------------------------------------------------------------------------
# add_trigger_job when scheduler not running
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_trigger_scheduler_not_running():
    """When scheduler is not running, add_trigger_job should return without error."""
    stopped_sched = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    # Do NOT start it — running property is False

    with patch("app.scheduler.get_scheduler", return_value=stopped_sched):
        # Should not raise
        add_trigger_job(uuid.uuid4(), "interval", {"interval_minutes": 5})

    # No jobs should be registered
    assert stopped_sched.get_jobs() == []


# ---------------------------------------------------------------------------
# remove_trigger_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_existing_job(running_scheduler: AsyncIOScheduler):
    trigger_id = uuid.uuid4()
    add_trigger_job(trigger_id, "interval", {"interval_minutes": 5})
    assert running_scheduler.get_job(f"trigger_{trigger_id}") is not None

    remove_trigger_job(trigger_id)
    assert running_scheduler.get_job(f"trigger_{trigger_id}") is None


@pytest.mark.asyncio
async def test_remove_nonexistent_job_no_error(running_scheduler: AsyncIOScheduler):
    """Removing a non-existent job should not raise."""
    remove_trigger_job(uuid.uuid4())  # Should be silent


# ---------------------------------------------------------------------------
# pause_trigger_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_job(running_scheduler: AsyncIOScheduler):
    trigger_id = uuid.uuid4()
    add_trigger_job(trigger_id, "interval", {"interval_minutes": 5})

    pause_trigger_job(trigger_id)

    job = running_scheduler.get_job(f"trigger_{trigger_id}")
    assert job is not None
    assert job.next_run_time is None  # paused jobs have no next_run_time


@pytest.mark.asyncio
async def test_pause_nonexistent_job_no_error():
    """Pausing a non-existent job should not raise."""
    pause_trigger_job(uuid.uuid4())


# ---------------------------------------------------------------------------
# resume_trigger_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_job(running_scheduler: AsyncIOScheduler):
    trigger_id = uuid.uuid4()
    add_trigger_job(trigger_id, "interval", {"interval_minutes": 5})

    pause_trigger_job(trigger_id)
    job = running_scheduler.get_job(f"trigger_{trigger_id}")
    assert job is not None
    assert job.next_run_time is None

    resume_trigger_job(trigger_id)
    job = running_scheduler.get_job(f"trigger_{trigger_id}")
    assert job is not None
    assert job.next_run_time is not None


@pytest.mark.asyncio
async def test_resume_nonexistent_job_no_error():
    """Resuming a non-existent job should not raise."""
    resume_trigger_job(uuid.uuid4())


# ---------------------------------------------------------------------------
# W3-out M4 — EventBroker eviction job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_broker_eviction_job_adds_60s_interval(
    running_scheduler: AsyncIOScheduler,
) -> None:
    register_broker_eviction_job()
    job = running_scheduler.get_job(BROKER_EVICTION_JOB_ID)
    assert job is not None
    assert job.trigger.interval.total_seconds() == 60


@pytest.mark.asyncio
async def test_register_broker_eviction_job_replaces_existing(
    running_scheduler: AsyncIOScheduler,
) -> None:
    """Idempotent: 두 번 호출해도 job 이 하나만 존재."""
    register_broker_eviction_job()
    register_broker_eviction_job()
    jobs = [j for j in running_scheduler.get_jobs() if j.id == BROKER_EVICTION_JOB_ID]
    assert len(jobs) == 1


@pytest.mark.asyncio
async def test_register_broker_eviction_job_skips_when_scheduler_stopped() -> None:
    stopped = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    with patch("app.scheduler.get_scheduler", return_value=stopped):
        register_broker_eviction_job()  # must not raise
    assert stopped.get_jobs() == []


def test_evict_expired_brokers_drops_closed_past_ttl() -> None:
    """Job 본체 — closed_at 이 충분히 과거면 dict 에서 pop."""
    from datetime import timedelta

    from app.agent_runtime.event_broker import BrokerRegistry

    custom = BrokerRegistry()
    b = custom.get_or_create("run-x")
    b.close()
    assert b.closed_at is not None
    b.closed_at = b.closed_at - timedelta(seconds=600)  # > 300s TTL

    with patch("app.agent_runtime.event_broker.registry", custom):
        evict_expired_brokers()

    assert custom.get("run-x") is None


def test_evict_expired_brokers_swallows_exceptions() -> None:
    """예외가 cron loop 를 죽이면 안 됨 — broad-except 가 다음 호출 보장."""

    class Boom:
        def evict_expired(self, ttl_seconds: int = 300) -> int:
            raise RuntimeError("boom")

    with patch("app.agent_runtime.event_broker.registry", Boom()):
        evict_expired_brokers()  # must not raise


@pytest.mark.asyncio
async def test_register_conversation_run_stale_sweep_job_adds_interval(
    running_scheduler: AsyncIOScheduler,
) -> None:
    register_conversation_run_stale_sweep_job()
    job = running_scheduler.get_job(CONVERSATION_RUN_STALE_SWEEP_JOB_ID)
    assert job is not None
    assert job.trigger.interval.total_seconds() == 60


@pytest.mark.asyncio
async def test_register_conversation_run_stale_sweep_job_replaces_existing(
    running_scheduler: AsyncIOScheduler,
) -> None:
    register_conversation_run_stale_sweep_job()
    register_conversation_run_stale_sweep_job()
    jobs = [
        job for job in running_scheduler.get_jobs() if job.id == CONVERSATION_RUN_STALE_SWEEP_JOB_ID
    ]
    assert len(jobs) == 1


async def _seed_run_for_stale_sweep(
    db: AsyncSession,
    *,
    worker_instance_id: str,
) -> ConversationRun:
    user = User(id=TEST_USER_ID, email=f"{uuid.uuid4()}@scheduler.test", name="Scheduler")
    db.add(user)
    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()
    agent = Agent(
        user_id=user.id,
        name="Scheduler Agent",
        system_prompt="You are helpful.",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()
    conversation = Conversation(agent_id=agent.id, title="stale sweep")
    db.add(conversation)
    await db.flush()
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=user.id,
        source="chat",
        input_preview="slow",
    )
    await conversation_run_service.transition_run(
        db,
        run,
        "running",
        worker_instance_id=worker_instance_id,
    )
    run.heartbeat_at = run_utc_now_naive() - timedelta(minutes=20)
    return run


@pytest.mark.asyncio
async def test_stale_sweep_preserves_live_registry_tasks(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = RunTaskRegistry(worker_instance_id="current-worker")
    reset_run_task_registry_for_tests(registry)
    monkeypatch.setattr("app.scheduler.async_session", TestSession)
    run = await _seed_run_for_stale_sweep(db, worker_instance_id=registry.worker_instance_id)
    await db.commit()
    blocker = asyncio.Event()
    task = asyncio.create_task(blocker.wait())
    registry.start(run.id, task)

    try:
        await sweep_stale_conversation_runs()
        await db.refresh(run)
        assert run.status == "running"
        assert run.is_active is True
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        reset_run_task_registry_for_tests()


@pytest.mark.asyncio
async def test_stale_sweep_marks_prior_worker_runs_stale(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = RunTaskRegistry(worker_instance_id="current-worker")
    reset_run_task_registry_for_tests(registry)
    monkeypatch.setattr("app.scheduler.async_session", TestSession)
    run = await _seed_run_for_stale_sweep(db, worker_instance_id="dead-worker")
    await db.commit()

    try:
        await sweep_stale_conversation_runs()
        await db.refresh(run)
        assert run.status == "stale"
        assert run.is_active is False
    finally:
        reset_run_task_registry_for_tests()


# ---------------------------------------------------------------------------
# Model catalog update jobs
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_register_catalog_update_job_bootstraps_when_catalog_missing(
    running_scheduler: AsyncIOScheduler,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    catalog_path = tmp_path / "catalog.json"
    monkeypatch.setattr(
        "app.services.model_catalog_updater.get_catalog_path",
        lambda: catalog_path,
    )

    register_catalog_update_job()

    assert running_scheduler.get_job(CATALOG_UPDATE_JOB_ID) is not None
    assert running_scheduler.get_job(CATALOG_BOOTSTRAP_JOB_ID) is not None


@pytest.mark.anyio
async def test_register_catalog_update_job_skips_bootstrap_when_catalog_exists(
    running_scheduler: AsyncIOScheduler,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "app.services.model_catalog_updater.get_catalog_path",
        lambda: catalog_path,
    )

    register_catalog_update_job()

    assert running_scheduler.get_job(CATALOG_UPDATE_JOB_ID) is not None
    assert running_scheduler.get_job(CATALOG_BOOTSTRAP_JOB_ID) is None


# ---------------------------------------------------------------------------
# get_scheduler singleton
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_scheduler_returns_singleton():
    """get_scheduler should return the same instance on repeated calls."""
    import app.scheduler as sched_mod

    # Reset the global
    original = sched_mod._scheduler
    try:
        sched_mod._scheduler = None
        s1 = sched_mod.get_scheduler()
        s2 = sched_mod.get_scheduler()
        assert s1 is s2
    finally:
        sched_mod._scheduler = original
