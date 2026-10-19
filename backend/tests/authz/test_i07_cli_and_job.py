"""I-07: the ``tenant_purge`` leader job (off by default) and the operator CLI."""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.config import Settings
from app.models.audit_event import AuditEvent
from app.models.organization import Tenant
from app.scheduler_job_ids import LeaderSchedulerJobId
from app.services.platform.tenant_data_cli import run
from tests.authz.i07_company_seed import seed_company
from tests.conftest import TestSession

JOB = LeaderSchedulerJobId.TENANT_PURGE


def test_the_purge_job_is_off_by_default_with_a_30_day_grace() -> None:
    assert Settings.model_fields["tenant_purge_enabled"].default is False
    assert Settings.model_fields["tenant_deletion_grace_days"].default == 30
    assert Settings.model_fields["tenant_export_ttl_days"].default == 7


async def test_registration_follows_the_setting_and_targets_the_fenced_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.scheduler as scheduler_module
    import app.scheduler_runtime as runtime
    import app.scheduler_tenant_purge as purge_module

    scheduler = AsyncIOScheduler(jobstores={"default": MemoryJobStore()})
    scheduler.start()
    monkeypatch.setattr(scheduler_module, "get_scheduler", lambda: scheduler)
    monkeypatch.setattr(purge_module, "get_scheduler", lambda: scheduler)
    try:
        monkeypatch.setattr(purge_module.settings, "tenant_purge_enabled", True)
        purge_module.register_tenant_purge_job()
        job = scheduler.get_job(JOB)
        assert job is not None and job.func is runtime.run_leader_scheduler_job
        assert job.args == (JOB,) and job.max_instances == 1
        monkeypatch.setattr(purge_module.settings, "tenant_purge_enabled", False)
        purge_module.register_tenant_purge_job()
        assert scheduler.get_job(JOB) is None
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.parametrize("healthy", [True, False])
@pytest.mark.parametrize(
    ("job", "body_name"),
    [(JOB, "tenant_purge_run"), (LeaderSchedulerJobId.TENANT_EXPORT_GC, "tenant_export_gc_run")],
)
async def test_the_jobs_are_fenced_by_leadership(
    monkeypatch: pytest.MonkeyPatch, healthy: bool, job: LeaderSchedulerJobId, body_name: str
) -> None:
    import app.scheduler_runtime as runtime

    body = AsyncMock()
    monkeypatch.setattr(runtime, "scheduler_leader_is_healthy", AsyncMock(return_value=healthy))
    monkeypatch.setattr(runtime, body_name, body)
    await runtime.run_leader_scheduler_job(job)
    assert body.await_count == int(healthy)


async def test_cli_schedules_cancels_exports_and_purges_only_when_due(tmp_path: Path) -> None:
    async with TestSession() as db:
        company = await seed_company(db, tmp_path, "cli")
    tenant = str(company.tenant_id)

    code, out = await run(["schedule-deletion", "--tenant", tenant, "--slug", "bad"], TestSession)
    assert (code, out["error"]) == (1, "tenant_deletion_slug_mismatch")
    code, out = await run(
        ["schedule-deletion", "--tenant", tenant, "--slug", company.slug], TestSession
    )
    assert code == 0 and out["status"] == "deletion_scheduled" and out["deletion_due_at"]
    code, out = await run(["cancel-deletion", "--tenant", tenant], TestSession)
    assert (code, out["status"], out["deletion_due_at"]) == (0, "suspended", None)

    code, out = await run(
        ["export", "--tenant", tenant, "--out", str(tmp_path / "out")], TestSession
    )
    assert code == 0
    with zipfile.ZipFile(str(out["path"])) as archive:
        assert "manifest.json" in archive.namelist()
        assert "files/skills/" in "".join(archive.namelist())

    await run(["schedule-deletion", "--tenant", tenant, "--slug", company.slug], TestSession)
    code, out = await run(["purge", "--tenant", tenant], TestSession)
    assert code == 1  # not due for 30 days
    async with TestSession() as db:
        row = await db.get(Tenant, company.tenant_id)
        assert row is not None
        row.deletion_due_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    code, out = await run(["purge", "--tenant", tenant], TestSession)
    assert code == 0 and out["purged"][0]["tenant_id"] == tenant  # type: ignore[index]
    async with TestSession() as db:
        assert await db.get(Tenant, company.tenant_id) is None
        actions = set(
            await db.scalars(select(AuditEvent.action).where(AuditEvent.target_id == tenant))
        )
        assert actions == {"tenant.deleted"}
        event = await db.scalar(select(AuditEvent).where(AuditEvent.action == "tenant.deleted"))
        assert event is not None and event.actor_type == "system"
        assert event.actor_label == "operator_cli"
