"""E-08: ``retention_sweep`` is a leader job, off by default (plan 6.2, 14.1)."""

from unittest.mock import AsyncMock

import pytest
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import Settings
from app.scheduler_job_ids import LeaderSchedulerJobId

JOB = LeaderSchedulerJobId.RETENTION_SWEEP


def test_the_sweep_is_off_by_default() -> None:
    assert Settings.model_fields["retention_sweep_enabled"].default is False


async def test_registration_follows_the_setting_and_targets_the_fenced_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.scheduler as scheduler_module
    import app.scheduler_runtime as runtime

    scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    scheduler.start()
    monkeypatch.setattr(scheduler_module, "get_scheduler", lambda: scheduler)
    try:
        monkeypatch.setattr(scheduler_module.settings, "retention_sweep_enabled", True)
        scheduler_module.register_retention_sweep_job()
        job = scheduler.get_job(JOB)
        assert job is not None and job.func is runtime.run_leader_scheduler_job
        assert job.args == (JOB,) and job.max_instances == 1
        monkeypatch.setattr(scheduler_module.settings, "retention_sweep_enabled", False)
        scheduler_module.register_retention_sweep_job()
        assert scheduler.get_job(JOB) is None
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.parametrize("healthy", [True, False])
async def test_the_sweep_is_fenced_by_leadership(
    monkeypatch: pytest.MonkeyPatch, healthy: bool
) -> None:
    import app.scheduler_runtime as runtime

    body = AsyncMock()
    monkeypatch.setattr(runtime, "scheduler_leader_is_healthy", AsyncMock(return_value=healthy))
    monkeypatch.setattr(runtime, "retention_sweep_run", body)
    await runtime.run_leader_scheduler_job(JOB)
    assert body.await_count == int(healthy)
