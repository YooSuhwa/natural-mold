"""Operator system status: components checked against the install profile (app/install)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.guardrails import model_settings
from app.install import status
from app.install.profiles import COMPONENTS, PROFILES
from app.jobs import service as jobs
from app.models.job import Job, JobWorker
from app.storage.object_store import reset_object_store
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, seed_agent

URL = "/api/system/status"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    monkeypatch.setattr(settings, "install_profile", "minimal")
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_local_root", str(tmp_path / "objects"))
    monkeypatch.setattr(settings, "mcp_runtime_url", "")
    monkeypatch.setattr(settings, "skill_sandbox_enabled", False)

    async def no_detector() -> model_settings.ModelService:
        return model_settings.ModelService(False, "", "environment")

    monkeypatch.setattr(model_settings, "current", no_detector)
    reset_object_store()
    yield
    reset_object_store()


def _by_key(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["key"]: c for c in body["components"]}


async def _beat(db: AsyncSession, worker_id: str, *, age: timedelta) -> None:
    moment = jobs.now() - age
    db.add(
        JobWorker(
            worker_id=worker_id,
            hostname="host",
            job_types=["system.probe"],
            started_at=moment,
            heartbeat_at=moment,
        )
    )
    await db.commit()


async def _job(db: AsyncSession, **fields: Any) -> None:
    context = unit_context(TEST_USER_ID)
    fields.setdefault("run_after", jobs.now())
    db.add(Job(tenant_id=context.tenant_id, org_id=context.org_id, type="t", **fields))
    await db.commit()


async def test_a_fresh_install_reports_the_missing_worker_as_an_error(
    client: AsyncClient,
) -> None:
    body = (await client.get(URL)).json()

    components = _by_key(body)
    assert body["profile"] == "minimal"
    assert body["verdict"] == "error"
    assert set(components) == set(COMPONENTS)
    assert components["database"]["verdict"] == "ok"
    assert components["object_store"]["verdict"] == "ok"
    assert (components["worker"]["found"], components["worker"]["verdict"]) == ("off", "error")
    # pgvector is recommended for this profile; the test database is SQLite.
    assert (components["pgvector"]["found"], components["pgvector"]["verdict"]) == (
        "off",
        "warning",
    )
    # Optional and off is fine.
    assert components["mcp_runtime"]["verdict"] == "ok"
    assert components["mcp_runtime"]["found"] == "off"
    assert body["workers"] == []


async def test_a_live_worker_turns_the_worker_ok_and_a_silent_one_is_listed_not_live(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _beat(db, "live", age=timedelta(seconds=1))
    await _beat(db, "gone", age=timedelta(minutes=10))

    body = (await client.get(URL)).json()

    worker = _by_key(body)["worker"]
    assert (worker["verdict"], worker["detail"]) == ("ok", "1 live")
    assert {w["worker_id"]: w["live"] for w in body["workers"]} == {"live": True, "gone": False}


async def test_jobs_waiting_too_long_with_a_live_worker_are_an_error(
    client: AsyncClient, db: AsyncSession
) -> None:
    await seed_agent(db)
    await _beat(db, "live", age=timedelta(seconds=1))
    await _job(db, run_after=jobs.now() - timedelta(minutes=5))
    await _job(db, run_after=jobs.now())
    await _job(db, state="running")
    await _job(db, state="failed", finished_at=jobs.now() - timedelta(hours=1))
    await _job(db, state="failed", finished_at=jobs.now() - timedelta(days=2))

    body = (await client.get(URL)).json()

    assert body["queue"] == {"queued": 2, "running": 1, "overdue": 1, "failed_last_day": 1}
    worker = _by_key(body)["worker"]
    assert worker["verdict"] == "error"
    assert "1 jobs waiting over a minute" in worker["detail"]


async def test_the_kubernetes_profile_needs_shared_storage_and_the_runtime(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "install_profile", "kubernetes")
    await _beat(db, "live", age=timedelta(seconds=1))

    body = (await client.get(URL)).json()

    components = _by_key(body)
    assert body["profile"] == "kubernetes"
    assert components["object_store"]["verdict"] == "error"
    assert "S3-compatible" in components["object_store"]["detail"]
    assert (components["mcp_runtime"]["requirement"], components["mcp_runtime"]["verdict"]) == (
        "required",
        "error",
    )
    assert components["skill_sandbox"]["verdict"] == "error"


async def test_a_failing_check_is_reported_and_does_not_hide_the_others(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken() -> status.Check:
        raise ConnectionResetError

    monkeypatch.setattr(status, "_mcp_runtime", broken)
    monkeypatch.setattr(settings, "install_profile", "external_api")

    found = await status.collect(db)

    by_key = {c.key: c for c in found.components}
    assert (by_key["mcp_runtime"].found, by_key["mcp_runtime"].detail) == (
        "error",
        "ConnectionResetError",
    )
    assert by_key["mcp_runtime"].verdict == "warning"  # recommended here
    assert by_key["database"].verdict == "ok"


async def test_a_check_that_hangs_is_cut_off(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def hangs() -> status.Check:
        await asyncio.sleep(5)
        return status.Check("ok", "late")

    monkeypatch.setattr(status, "_pii_detector", hangs)
    monkeypatch.setattr(status, "CHECK_TIMEOUT_SECONDS", 0.05)

    found = await status.collect(db)

    pii = next(c for c in found.components if c.key == "pii_detector")
    assert (pii.found, pii.detail) == ("error", "no answer in 0 s")
    assert pii.verdict == "warning"  # optional, but on and failing


async def test_a_detector_that_answers_is_ok(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def on() -> model_settings.ModelService:
        return model_settings.ModelService(True, "http://10.0.0.5:8095", "environment")

    async def answers(url: str, api_key: str) -> model_settings.ProbeResult:
        return model_settings.ProbeResult("ok", 12, "kanana", 2)

    monkeypatch.setattr(model_settings, "current", on)
    monkeypatch.setattr(model_settings, "probe", answers)

    found = await status.collect(db, profile_key="gpu")

    pii = next(c for c in found.components if c.key == "pii_detector")
    assert (pii.found, pii.verdict, pii.detail) == ("ok", "ok", "kanana (12 ms)")


@pytest.mark.parametrize(
    ("requirement", "found", "verdict"),
    [
        ("required", "ok", "ok"),
        ("required", "off", "error"),
        ("required", "error", "error"),
        ("recommended", "off", "warning"),
        ("recommended", "error", "warning"),
        ("optional", "off", "ok"),
        ("optional", "error", "warning"),
    ],
)
def test_the_verdict_follows_the_requirement(requirement: Any, found: Any, verdict: str) -> None:
    assert status.verdict_for(requirement, status.Check(found, "")) == verdict


def test_every_profile_requires_the_core_and_names_every_component() -> None:
    for profile in PROFILES.values():
        assert tuple(profile.requirements) == COMPONENTS
        for core in ("database", "worker", "object_store"):
            assert profile.requirements[core] == "required"
    assert [key for key, p in PROFILES.items() if p.shared_storage] == ["kubernetes"]


@pytest.fixture
async def non_super_client() -> AsyncIterator[AsyncClient]:
    from httpx import ASGITransport

    from app.dependencies import CurrentUser, get_current_user, get_db
    from app.main import create_app
    from tests.conftest import override_get_db

    async def regular() -> CurrentUser:
        return CurrentUser(
            id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            email="user@test.com",
            name="Regular User",
            is_super_user=False,
        )

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = regular
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_operators_only(non_super_client: AsyncClient) -> None:
    assert (await non_super_client.get(URL)).status_code == 403
