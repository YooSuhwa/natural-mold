"""PostgreSQL lane, clients and workload for operator suspension (parent Task 13).

Canonical runner only (``_runner_dsns``). Mirrors the Task 10 lane without
importing its native sources (the Task 10 source contract forbids that): every
``async_session``/``TestSession`` alias in loaded ``app.*``/``tests.authz.*``
modules is rebound to one runner-owned engine, the prepared lease flag is on,
and teardown asserts no active lease, no live run task and no checked-out
connection. Uses the globally registered platform lifecycle/directory and
company overview routers, one SaaS operator and the seeded company member.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.database as database
from app.agent_api import service as agent_api_service
from app.agent_runtime.prepared_run_registry import get_prepared_run_registry
from app.auth.jwt import create_access_token, create_csrf_token
from app.authz.org_context import OrgContext
from app.config import settings
from app.dependencies import get_db
from app.main import create_app
from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.organization import Tenant
from app.models.user import User
from app.schemas.agent_api import AgentApiKeyCreate, AgentDeploymentCreate
from app.services import conversation_run_worker
from app.services.tenants.versioning import etag
from tests import conftest
from tests.authz.company_administration_migration_fixture import _runner_dsns
from tests.authz.execution_gateway_testkit import GatewayGraph, seed_graph

Sessions = async_sessionmaker[database.ShieldedAsyncSession]


@dataclass(frozen=True, slots=True)
class SuspensionLane:
    sessions: Sessions
    engine: AsyncEngine
    monkeypatch: pytest.MonkeyPatch


def _rebind(monkeypatch: pytest.MonkeyPatch, sessions: Sessions) -> None:
    stale = {id(database.async_session), id(conftest.TestSession)}
    for name, module in list(sys.modules.items()):
        if module is None or not name.startswith(("app.", "tests.authz.")):
            continue
        for attribute in ("async_session", "TestSession"):
            if id(getattr(module, attribute, None)) in stale:
                monkeypatch.setattr(module, attribute, sessions)


@pytest.fixture
async def suspension_lane(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[SuspensionLane]:
    engine = create_async_engine(
        _runner_dsns().async_url,
        connect_args={"server_settings": {"application_name": "task13_native"}},
    )
    sessions = async_sessionmaker(
        engine, class_=database.ShieldedAsyncSession, expire_on_commit=False
    )
    _rebind(monkeypatch, sessions)
    monkeypatch.setattr(settings, "prepared_run_lease_enabled", True)
    monkeypatch.setattr(settings, "deployment_mode", "multi_tenant")
    try:
        yield SuspensionLane(sessions, engine, monkeypatch)
        assert get_prepared_run_registry().active_run_ids() == set()
        assert conversation_run_worker.get_run_task_registry().active_run_ids() == set()
        assert engine.pool.checkedout() == 0  # type: ignore[attr-defined]
    finally:
        await engine.dispose()


@dataclass(frozen=True, slots=True)
class NativeSuspension:
    lane: SuspensionLane
    graph: GatewayGraph
    operator: AsyncClient
    rival: AsyncClient
    member: AsyncClient
    trigger_id: UUID
    deployment_public_id: str
    api_key: str

    @property
    def context(self) -> OrgContext:
        return self.graph.context

    async def version(self) -> str:
        async with self.lane.sessions() as db:
            tenant = await db.get(Tenant, self.context.tenant_id)
            assert tenant is not None
            return etag(tenant.updated_at)

    async def status(self) -> str:
        async with self.lane.sessions() as db:
            tenant = await db.get(Tenant, self.context.tenant_id)
            assert tenant is not None
            return tenant.status

    async def transition(
        self, action: str, *, client: AsyncClient | None = None, version: str | None = None
    ) -> Response:
        return await (client or self.operator).post(
            f"/api/admin/tenants/{self.context.tenant_id}/{action}",
            headers={"If-Match": version or await self.version()},
        )


def _app(sessions: Sessions):
    app = create_app()

    async def database_session() -> AsyncGenerator[AsyncSession, None]:
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database_session
    return app


@asynccontextmanager
async def _client(app: object, user_id: UUID, *, super_user: bool) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://task13") as client:
        csrf = create_csrf_token(user_id)
        client.cookies.set(
            settings.cookie_name_access, create_access_token(user_id, is_super_user=super_user)
        )
        client.cookies.set(settings.cookie_name_csrf, csrf)
        client.headers["X-CSRF-Token"] = csrf
        yield client


async def _seed(db: AsyncSession, graph: GatewayGraph) -> tuple[UUID, str, str]:
    context = graph.context
    agent = await db.get(Agent, graph.agent_id)
    assert agent is not None
    agent.identity_mode = "fixed"
    trigger = AgentTrigger(
        agent_id=graph.agent_id,
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        name="Task 13 native",
        trigger_type="interval",
        schedule_config={"minutes": 30},
        input_message="scheduled hello",
        timezone="Asia/Seoul",
        conversation_policy="new_per_run",
        status="active",
    )
    db.add(trigger)
    await db.commit()
    deployment = await agent_api_service.create_deployment(
        db, context, AgentDeploymentCreate(agent_id=graph.agent_id)
    )
    _key, cleartext = await agent_api_service.create_api_key(
        db,
        context,
        AgentApiKeyCreate(name="Task 13", scopes=["read", "invoke"], allow_all_deployments=True),
    )
    return trigger.id, deployment.public_id, cleartext


@asynccontextmanager
async def native_suspension(lane: SuspensionLane) -> AsyncIterator[NativeSuspension]:
    async with lane.sessions() as db:
        graph = await seed_graph(db, conversations=2)
        operator = User(
            email=f"task13-native-{uuid4().hex}@example.com",
            name="Task 13 operator",
            is_active=True,
            is_super_user=True,
        )
        db.add(operator)
        await db.commit()
        trigger_id, public_id, api_key = await _seed(db, graph)
    app = _app(lane.sessions)
    try:
        async with (
            _client(app, operator.id, super_user=True) as operator_client,
            _client(app, operator.id, super_user=True) as rival,
            _client(app, graph.context.user_id, super_user=False) as member,
        ):
            yield NativeSuspension(
                lane, graph, operator_client, rival, member, trigger_id, public_id, api_key
            )
    finally:
        app.dependency_overrides.clear()
