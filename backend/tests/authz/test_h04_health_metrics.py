"""H-04 OpenFGA health, 14.3 log metrics and the ``h04_authz_reconcile_runs`` migration."""

import asyncio
import importlib.util
import logging
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import AsyncClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from alembic import op as alembic_op
from app.authz import ops_health
from app.authz.client import AuthzUnavailable, FgaClient
from app.authz.config import AuthzSettings
from app.authz.error_codes import agent_not_ready
from app.config import settings

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


class _Store:
    def __init__(self, *, fail: bool = False, delay: float = 0.0) -> None:
        self.fail, self.delay, self.calls = fail, delay, 0

    async def ping(self) -> None:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.fail:
            raise AuthzUnavailable("ping")

    def opener(self, _config: AuthzSettings) -> Any:
        store = self

        class _Ctx:
            async def __aenter__(self) -> "_Store":
                return store

            async def __aexit__(self, *_exc: object) -> None:
                return None

        return _Ctx()


@pytest.fixture(autouse=True)
def _fresh() -> None:
    ops_health.reset()


def _config(**values: object) -> AuthzSettings:
    return AuthzSettings.model_validate({"authz_mode": "shadow", **values})


async def test_legacy_health_is_unchanged_and_never_probes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "authz_mode", "legacy")
    store = _Store(fail=True)
    monkeypatch.setattr(ops_health, "open_client", store.opener)
    assert (await client.get("/api/health")).json() == {"status": "ok"}
    assert store.calls == 0


async def test_health_reports_authz_outside_legacy(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "authz_mode", "shadow")
    monkeypatch.setattr(ops_health, "open_client", _Store().opener)
    assert (await client.get("/api/health")).json() == {"status": "ok", "authz": "ok"}


async def test_probe_states_cache_and_down_alert(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.authz.ops_health")
    healthy = _Store()
    first = await ops_health.fga_health(_config(), opener=healthy.opener)
    again = await ops_health.fga_health(_config(), opener=healthy.opener)
    assert (first.status, healthy.calls, again) == ("ok", 1, first)  # cached within TTL
    assert first.latency_ms is not None and first.last_success_at is not None

    ops_health.reset()
    slow_store = _Store(delay=0.01)
    slow = await ops_health.fga_health(_config(openfga_timeout_ms=2), opener=slow_store.opener)
    assert slow.status == "degraded"

    ops_health.reset()
    down = _Store(fail=True)
    assert (await ops_health.fga_health(_config(), opener=down.opener)).status == "down"
    cached = ops_health._cached
    assert cached is not None and cached.checked_at is not None
    expired = cached.checked_at - ops_health.TTL
    ops_health._cached = cached.model_copy(update={"checked_at": expired})
    assert (await ops_health.fga_health(_config(), opener=down.opener)).status == "down"
    messages = [record.getMessage() for record in caplog.records]
    assert messages.count("authz_openfga_down") == 1  # once per transition
    assert "authz_check_latency_ms" in messages


async def test_client_errors_and_blocked_runs_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    client = FgaClient(sdk=None, timeout_ms=100)  # type: ignore[arg-type]

    async def timeout() -> None:
        raise TimeoutError

    with pytest.raises(AuthzUnavailable):
        await client._run("check", timeout())
    error = next(r for r in caplog.records if r.getMessage() == "authz_check_errors")
    assert (error.__dict__["operation"], error.__dict__["error"]) == ("check", "TimeoutError")
    agent_not_ready([{"kind": "tool", "result": "delegation_broken"}])
    blocked = next(r for r in caplog.records if r.getMessage() == "readiness_blocked_runs")
    assert blocked.__dict__["items"] == 1


def _migration() -> ModuleType:
    path = VERSIONS / "h04_authz_reconcile_runs.py"
    spec = importlib.util.spec_from_file_location("h04_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_chain_one_active_run_and_downgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _migration()
    assert (module.revision, module.down_revision) == (
        "h04_authz_reconcile_runs",
        "h08_sso_hardening",
    )
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE users (id CHAR(32) PRIMARY KEY)")
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(alembic_op, "_proxy", operations, raising=False)
        module.upgrade()
        insert = (
            "INSERT INTO authz_reconcile_runs (id, status, trigger, mode, active) "
            "VALUES (?, ?, 'manual', 'repair', ?)"
        )
        connection.exec_driver_sql(insert, (uuid.uuid4().hex, "succeeded", None))
        connection.exec_driver_sql(insert, (uuid.uuid4().hex, "failed", None))
        connection.exec_driver_sql(insert, (uuid.uuid4().hex, "running", True))
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.exec_driver_sql(insert, (uuid.uuid4().hex, "queued", True))
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.exec_driver_sql(insert, (uuid.uuid4().hex, "paused", None))
        module.downgrade()
        assert "authz_reconcile_runs" not in inspect(connection).get_table_names()
