"""PostgreSQL-only lane for the Task 10 native prepared-runtime modules (internal I23).

Canonical runner only: ``_runner_dsns`` refuses to build a lane without the
runner-owned migrated database. Every ``async_session``/``TestSession`` alias in
already-imported ``app.*`` and ``tests.authz.*`` modules is rebound to one
per-test engine, so the real workers, gateways, permits and testkits all run
on separate PostgreSQL connections (real row locks). The lease flag is on, the
scheduler is recorded instead of started, and the app-local lifecycle router is
mounted for real archive/restore HTTP. Teardown asserts runtime cleanup: no
active lease, no live run task, no active owned run, every pooled connection
returned.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.database as database
import tests.authz.prepared_agent_api_testkit
import tests.authz.prepared_queue_testkit
import tests.authz.prepared_trigger_testkit  # noqa: F401 - rebound below
from app import scheduler
from app.agent_runtime.prepared_child_launch import get_child_launch_ledger
from app.agent_runtime.prepared_run_registry import get_prepared_run_registry
from app.authz.org_context import OrgContext
from app.config import settings
from app.dependencies import get_db
from app.main import create_app
from app.models.conversation_run import ConversationRun
from app.routers.tenant_organization_lifecycle import router as lifecycle_router
from app.routers.tenant_organizations import router as organizations_router
from app.services import conversation_run_worker
from tests import conftest
from tests.authz.company_administration_migration_fixture import _runner_dsns
from tests.authz.task10_company_admin import CompanyAdmin, lifecycle, seed_company_admin

Sessions = async_sessionmaker[database.ShieldedAsyncSession]
SCHEDULER_HOOKS = ("add_trigger_job", "resume_trigger_job", "remove_trigger_job")


@dataclass
class Lane:
    sessions: Sessions
    engine: AsyncEngine
    client: AsyncClient
    monkeypatch: pytest.MonkeyPatch
    scheduler_calls: dict[str, list[tuple[Any, ...]]] = field(default_factory=dict)
    owned_runs: set[UUID] = field(default_factory=set)
    leased_runs: set[UUID] = field(default_factory=set)

    def rebind(self) -> None:
        """Re-point every session alias at this lane (call after a testkit install)."""

        rebind_sessions(self.monkeypatch, self.sessions)
        self.monkeypatch.setattr(settings, "prepared_run_lease_enabled", True)

    async def admin(self, context: OrgContext) -> CompanyAdmin:
        async with self.sessions() as db:
            return await seed_company_admin(db, context)

    async def lifecycle(self, admin: CompanyAdmin, action: str, **kw: Any) -> Response:
        return await lifecycle(self.client, self.sessions, admin, action, **kw)

    def track(self, *run_ids: UUID | None, leased: bool = False) -> None:
        ids = {run_id for run_id in run_ids if run_id is not None}
        self.owned_runs.update(ids)
        if leased:
            self.leased_runs.update(ids)

    async def run(self, run_id: UUID) -> ConversationRun:
        async with self.sessions() as db:
            row = await db.get(ConversationRun, run_id)
        assert row is not None
        return row


def rebind_sessions(monkeypatch: pytest.MonkeyPatch, sessions: Sessions) -> None:
    stale = {id(database.async_session), id(conftest.TestSession)}
    for name, module in list(sys.modules.items()):
        if module is None or not name.startswith(("app.", "tests.authz.")):
            continue
        for attribute in ("async_session", "TestSession"):
            if id(getattr(module, attribute, None)) in stale:
                monkeypatch.setattr(module, attribute, sessions)


def _app(sessions: Sessions) -> Any:
    app = create_app()
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    if "/api/tenants/{tenant_id}/organizations" not in paths:
        app.include_router(organizations_router)
    app.include_router(lifecycle_router)

    async def database_session() -> AsyncGenerator[AsyncSession, None]:
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database_session
    return app


def _record(lane: Lane, name: str) -> Callable[..., None]:
    lane.scheduler_calls[name] = []
    return lambda *args, **_kwargs: lane.scheduler_calls[name].append(args)


async def _assert_runtime_clean(lane: Lane) -> None:
    assert get_prepared_run_registry().active_run_ids() == set()
    assert conversation_run_worker.get_run_task_registry().active_run_ids() == set()
    assert all(get_child_launch_ledger().is_closed(run_id) for run_id in lane.leased_runs)
    if lane.owned_runs:
        async with lane.sessions() as db:
            active = await db.scalars(
                select(ConversationRun.id).where(
                    ConversationRun.id.in_(lane.owned_runs), ConversationRun.is_active.is_(True)
                )
            )
            assert list(active) == []
    assert lane.engine.pool.checkedout() == 0  # type: ignore[attr-defined]


@pytest.fixture
async def task10_lane(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Lane]:
    dsns = _runner_dsns()
    engine = create_async_engine(
        dsns.async_url, connect_args={"server_settings": {"application_name": "task10_native"}}
    )
    sessions = async_sessionmaker(
        engine, class_=database.ShieldedAsyncSession, expire_on_commit=False
    )
    app = _app(sessions)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://task10") as client:
        lane = Lane(sessions, engine, client, monkeypatch)
        for name in SCHEDULER_HOOKS:
            monkeypatch.setattr(scheduler, name, _record(lane, name))
        lane.rebind()
        try:
            yield lane
            await _assert_runtime_clean(lane)
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()
