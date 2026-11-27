"""I-06 identity gateway on real PostgreSQL with RLS forced and a non-owner app role.

Login, refresh, ``/me`` and org-context resolution go through the assembled app
whose request sessions are the production ``tenant_session(None)`` on the app
role. Without the identity scope every one of them would read zero rows.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import database
from app.auth.password import hash_password
from app.authz import runtime
from app.authz.fake import FakeAuthz
from app.authz.service import AuthzService
from app.config import settings
from app.database import platform_session, tenant_session
from app.dependencies import CurrentUser, get_db
from app.main import create_app
from app.models.agent import AGENT_RUNTIME_PROFILE_SKILL_BUILDER, Agent
from app.models.authz import AuthzOutbox
from app.models.organization import OrganizationMember
from app.models.user import User
from app.seed.e2e_scripted_model import seed_e2e_scripted_model
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab
from tests.authz.unit_scope_fixture import TEST_ORG_ID, ensure_unit_scope

pytestmark = pytest.mark.integration
PASSWORD = "correct horse battery staple"  # noqa: S105 - fixture-only account


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as value:
        yield value


@pytest.fixture(autouse=True)
def rls_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    monkeypatch.setattr(settings, "cookie_secure", False)


async def _seed_user(lab: RlsLab) -> tuple[UUID, str]:
    owner = create_async_engine(async_url(lab.admin_url))
    try:
        maker = async_sessionmaker(owner, class_=database.ShieldedAsyncSession)
        async with platform_session("seed.startup", maker=maker) as db:
            actor = CurrentUser(id=uuid4(), email=f"{uuid4().hex[:8]}@i06.example.com", name="I06")
            await ensure_unit_scope(db, actor)
            user = await db.get(User, actor.id)
            assert user is not None
            user.hashed_password = hash_password(PASSWORD)
            await db.commit()
            return actor.id, actor.email
    finally:
        await owner.dispose()


@pytest.fixture
async def client(lab: RlsLab) -> AsyncIterator[AsyncClient]:
    engine = create_async_engine(async_url(lab.app_url))
    # Same options as the production maker (app.database.async_session).
    maker = async_sessionmaker(engine, class_=database.ShieldedAsyncSession, expire_on_commit=False)
    app = create_app()

    async def request_session() -> AsyncGenerator[AsyncSession, None]:
        async with tenant_session(None, maker=maker) as db:  # production get_db semantics
            yield db

    app.dependency_overrides[get_db] = request_session
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://i06.test") as http:
            yield http
    finally:
        await engine.dispose()


async def test_login_refresh_me_and_org_context_work_under_forced_rls(
    lab: RlsLab, client: AsyncClient
) -> None:
    user_id, email = await _seed_user(lab)
    login = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    me = await client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["id"] == str(user_id)
    refresh = await client.post("/api/auth/refresh")
    assert refresh.status_code == 200, refresh.text
    client.headers["X-CSRF-Token"] = refresh.json()["csrf_token"]
    scoped = await client.get("/api/access-requests", params={"box": "outbox"})
    assert scoped.status_code == 200, scoped.text
    with lab.admin.connect() as connection:
        actions = set(
            connection.scalars(
                sa.text("SELECT action FROM audit_events WHERE actor_user_id = :u"),
                {"u": user_id},
            )
        )
    assert {"auth.login", "auth.refresh"} <= actions


async def test_wrong_credentials_are_still_audited_and_refused(
    lab: RlsLab, client: AsyncClient
) -> None:
    _user_id, email = await _seed_user(lab)
    refused = await client.post("/api/auth/login", json={"email": email, "password": "nope"})
    assert refused.status_code == 401
    with lab.admin.connect() as connection:
        failures = connection.scalar(
            sa.text("SELECT failed_login_attempts FROM users WHERE email = :e"), {"e": email}
        )
    assert failures == 1


async def test_two_first_builder_sessions_share_one_private_agent(
    lab: RlsLab, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    user_id, email = await _seed_user(lab)
    login = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    monkeypatch.setattr(settings, "e2e_scripted_model_enabled", True)
    # Exercise real lifecycle outbox writes without requiring a live tuple server.
    service = AuthzService(FakeAuthz(), mode="enforce", enforce_types=frozenset({"agent"}))
    monkeypatch.setattr(runtime, "runtime_authz", lambda: service)

    engine = create_async_engine(async_url(lab.admin_url))
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as db:
            assert await seed_e2e_scripted_model(db) is not None

        # Both authenticated requests may insert their sessions and miss the initial
        # agent lookup. Hold the membership row until both reach the execution prefix.
        async with asyncio.timeout(30), asyncio.TaskGroup() as requests, sessions.begin() as guard:
            await guard.get(OrganizationMember, (TEST_ORG_ID, user_id), with_for_update=True)
            tasks = [
                requests.create_task(
                    client.post(
                        "/api/skill-builder", json={"mode": "create", "user_request": prompt}
                    )
                )
                for prompt in ("first", "second")
            ]
            waiters = sa.text("""
                WITH RECURSIVE blocked(pid) AS (
                    SELECT pg_backend_pid()
                    UNION
                    SELECT activity.pid FROM pg_stat_activity activity, blocked
                    WHERE activity.datname = current_database()
                      AND blocked.pid = ANY(pg_blocking_pids(activity.pid))
                )
                SELECT count(*) - 1 FROM blocked
            """)
            while await guard.scalar(waiters) != len(tasks):
                for task in tasks:
                    if task.done():
                        pytest.fail(f"Request returned before the seed race: {task.result().text}")
                await guard.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                await asyncio.sleep(0.01)

        replies = [task.result() for task in tasks]
        assert [reply.status_code for reply in replies] == [201, 201], [
            reply.text for reply in replies
        ]
        first, second = (reply.json() for reply in replies)
        assert first["id"] != second["id"]
        assert first["conversation_id"] != second["conversation_id"]
        assert first["agent_id"] == second["agent_id"]
        async with sessions() as db:
            hidden = (
                await db.scalars(
                    sa.select(Agent.id).where(
                        Agent.user_id == user_id,
                        Agent.runtime_profile == AGENT_RUNTIME_PROFILE_SKILL_BUILDER,
                    )
                )
            ).all()
            assert list(hidden) == [UUID(first["agent_id"])]
            rows = await db.scalars(
                sa.select(AuthzOutbox.tuple_key).where(
                    AuthzOutbox.source_table == "agents", AuthzOutbox.source_id == hidden[0]
                )
            )
            agent, org = f"agent:{hidden[0]}", f"organization:{TEST_ORG_ID}"
            assert sorted(rows) == sorted(
                [
                    f"{org} org {agent}",
                    f"user:{user_id} owner {agent}",
                    f"{agent} resident_agent {org}",
                ]
            )
    finally:
        await engine.dispose()
