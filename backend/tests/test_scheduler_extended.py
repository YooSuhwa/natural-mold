"""Extended tests for app.scheduler — job management with MemoryJobStore."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.scheduler import (
    add_trigger_job,
    pause_trigger_job,
    remove_trigger_job,
    resume_trigger_job,
)


@pytest.fixture
async def running_scheduler() -> AsyncIOScheduler:
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
    assert job.next_run_time is None

    resume_trigger_job(trigger_id)
    job = running_scheduler.get_job(f"trigger_{trigger_id}")
    assert job.next_run_time is not None


@pytest.mark.asyncio
async def test_resume_nonexistent_job_no_error():
    """Resuming a non-existent job should not raise."""
    resume_trigger_job(uuid.uuid4())


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
