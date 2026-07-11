from __future__ import annotations

import os
import secrets
import uuid
from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Cipher V2 / new credential domain require ``ENCRYPTION_KEYS`` to be set
# before ``app.config.settings`` is evaluated. Set it at import time so any
# module that reads settings during collection sees a valid value.
os.environ.setdefault("ENCRYPTION_KEYS", secrets.token_hex(32))

from app.auth import password as password_module  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402
from app.dependencies import (  # noqa: E402
    CurrentUser,
    get_current_user,
    get_current_user_optional,
    get_db,
    verify_csrf,
)
from app.main import create_app  # noqa: E402
from app.rate_limit import limiter  # noqa: E402
from app.security import key_provider  # noqa: E402
from app.services import share_cache  # noqa: E402

# Rate limiting is meaningful in production but not in unit tests — keeping
# it on would couple assertions to slowapi's internal counters.
limiter.enabled = False

# Production bcrypt uses 12 rounds, which is correct for real auth but costs
# ~200ms per hash/verify on local pytest runs. Keep the same bcrypt algorithm
# in tests while dropping the work factor so auth-flow tests exercise behavior
# instead of spending most of their time in CPU-bound hashing.
password_module._pwd_context = CryptContext(  # noqa: SLF001
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=4,
)

# Make sure the settings instance reflects the environment variable above
# (``BaseSettings`` reads env at class-init time, but explicit assignment
# guarantees correctness if any other test mutated ``settings``).
if not getattr(settings, "encryption_keys", ""):
    settings.encryption_keys = os.environ["ENCRYPTION_KEYS"]
key_provider.reset_cache()

TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

engine = create_async_engine("sqlite+aiosqlite://", echo=False)
TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
_schema_ready = False


async def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _schema_ready = True


async def _clear_database() -> None:
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest.fixture(autouse=True)
async def setup_db():
    await _ensure_schema()
    # Snapshot cache leaks across tests otherwise — clear at boundary.
    share_cache.clear_all()
    yield
    await _clear_database()
    share_cache.clear_all()


@pytest.fixture(autouse=True)
def _stub_llm_credential_resolution(monkeypatch):
    """Bypass the per-user credential gate in chat/trigger tests.

    Production policy (ADR-016 §4.2): agent chat raises 422
    ``llm_credential_required`` when the owner hasn't registered an LLM key.
    Tests focus on routing/streaming/checkpoint logic and don't set up
    real credentials, so substitute a deterministic dummy key. Tests that
    exercise the resolver directly should override this fixture locally
    (``monkeypatch.undo()`` or per-test re-patch).
    """

    async def _fake_resolve(_db, _agent, **_kwargs):
        return "test-api-key"

    monkeypatch.setattr(
        "app.services.conversation_stream_service.resolve_llm_api_key_for_agent",
        _fake_resolve,
    )
    monkeypatch.setattr(
        "app.agent_runtime.trigger_executor.resolve_llm_api_key_for_agent",
        _fake_resolve,
    )
    monkeypatch.setattr(
        "app.services.agent_invocation_service.resolve_llm_api_key_for_agent",
        _fake_resolve,
    )


@pytest.fixture(autouse=True)
def _clear_event_broker_registry():
    """W3-out M2 — module-level ``event_broker.registry`` is process-local and
    persists across tests. Without this fixture, any test that creates a
    broker (test_chat_integration, test_conversations_router, etc.) leaks
    state to the next test, causing random-order failures.
    """
    from app.agent_runtime import event_broker

    event_broker.registry._clear()  # noqa: SLF001 — test-only reset
    yield
    event_broker.registry._clear()  # noqa: SLF001 — test-only reset


@pytest.fixture(autouse=True)
def _patch_conversation_run_worker_session(monkeypatch):
    """Detached chat run workers must use the same in-memory DB as router tests."""

    monkeypatch.setattr("app.services.conversation_run_worker.async_session", TestSession)
    monkeypatch.setattr("app.services.conversation_stream_service.async_session", TestSession)
    monkeypatch.setattr("app.services.skill_builder_finalize.async_session", TestSession)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSession() as session:
        yield session


async def override_get_current_user() -> CurrentUser:
    return CurrentUser(
        id=TEST_USER_ID,
        email="test@test.com",
        name="Test User",
        is_super_user=True,
    )


async def _bypass_verify_csrf() -> None:
    """Test-only CSRF bypass.

    The S3 multi-user work added ``verify_csrf`` to mutating routes — the
    legacy ``client`` fixture didn't carry the cookie/header pair, so
    every legacy test would 403. We override the dependency to a no-op
    here so existing assertions keep their original semantics. New
    cookie-flow tests should use the unmodified app via ``raw_client``
    (see below) to exercise the real CSRF + JWT path.
    """

    return


@pytest.fixture(scope="session")
def test_app() -> FastAPI:
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_current_user_optional] = override_get_current_user
    app.dependency_overrides[verify_csrf] = _bypass_verify_csrf
    return app


@pytest.fixture(scope="session")
def raw_test_app() -> FastAPI:
    """App without auth/CSRF overrides — exercises real cookie + JWT flow."""

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    return app


@pytest.fixture
async def client(test_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def raw_client(raw_test_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """App without auth/CSRF overrides — exercises real cookie + JWT flow.

    Used by /api/auth tests and the multi-user isolation matrix.
    """

    transport = ASGITransport(app=raw_test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSession() as session:
        yield session


# ---------------------------------------------------------------------------
# Auth-related row builders (shared between test_user_cleanup, test_refresh_*).
# Factory-as-fixture so each call mints a unique row without collision.
# ---------------------------------------------------------------------------


def make_user(db: AsyncSession, *, email: str | None = None, **kwargs):
    """Persist a minimal active ``User`` and flush. Returns the row."""

    from app.models.user import User

    user = User(
        id=uuid.uuid4(),
        email=email or f"u-{uuid.uuid4().hex[:8]}@test.com",
        name=kwargs.pop("name", "U"),
        hashed_password=kwargs.pop("hashed_password", "h"),
        is_active=kwargs.pop("is_active", True),
        is_super_user=kwargs.pop("is_super_user", False),
        **kwargs,
    )
    db.add(user)

    async def _flush_and_return():
        await db.flush()
        return user

    return _flush_and_return()


def make_refresh_token(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    expires_at=None,
    revoked: bool = False,
):
    """Persist a minimal ``RefreshToken`` row and flush. Returns the row.

    Defaults to a 14-day-future ``expires_at`` matching pre-existing test
    fixtures. Tests for GC pass an explicit (often backdated) value.
    """

    from datetime import UTC, datetime, timedelta

    from app.models.refresh_token import RefreshToken

    row = RefreshToken(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash=uuid.uuid4().hex * 2,  # 64-char unique
        issued_at=datetime.now(UTC),
        expires_at=expires_at or datetime.now(UTC) + timedelta(days=14),
        revoked_at=datetime.now(UTC) if revoked else None,
    )
    db.add(row)

    async def _flush_and_return():
        await db.flush()
        return row

    return _flush_and_return()


# ---------------------------------------------------------------------------
# ORM seed factories (BE-D7). New tests must use these instead of inlining
# ``Model(provider="openai", ...)`` / local ``_seed_agent`` helpers; existing
# tests migrate opportunistically (e.g. when a schema change would touch them
# anyway). Marketplace object-graph builders (``_make_agent_item*``) and
# credential-binding-specific helpers stay local by design — they encode
# domain shape, not seed boilerplate.
# ---------------------------------------------------------------------------


def make_model(
    db: AsyncSession,
    *,
    provider: str = "openai",
    model_name: str = "gpt-4o",
    display_name: str = "GPT-4o",
    **kwargs,
):
    """Persist a minimal ``Model`` row and flush. Returns the row."""

    from app.models.model import Model

    model = Model(
        provider=provider,
        model_name=model_name,
        display_name=display_name,
        **kwargs,
    )
    db.add(model)

    async def _flush_and_return():
        await db.flush()
        return model

    return _flush_and_return()


def make_agent(
    db: AsyncSession,
    *,
    user_id: uuid.UUID = TEST_USER_ID,
    model_id: uuid.UUID | None = None,
    name: str = "Test Agent",
    system_prompt: str = "You are helpful.",
    **kwargs,
):
    """Persist a minimal ``Agent`` row and flush. Returns the row."""

    from app.models.agent import Agent

    agent = Agent(
        user_id=user_id,
        name=name,
        system_prompt=system_prompt,
        model_id=model_id,
        **kwargs,
    )
    db.add(agent)

    async def _flush_and_return():
        await db.flush()
        return agent

    return _flush_and_return()


async def seed_agent(
    db: AsyncSession,
    *,
    user_id: uuid.UUID = TEST_USER_ID,
    name: str = "Test Agent",
    system_prompt: str = "You are helpful.",
    **agent_kwargs,
):
    """Seed the User(get-or-create) + Model + Agent trio most router tests need.

    Returns ``(user, model, agent)``. For ``TEST_USER_ID`` the user row mirrors
    the ``override_get_current_user`` identity (``test@test.com``) so email
    assertions stay consistent.
    """

    from app.models.user import User

    user = await db.get(User, user_id)
    if user is None:
        if user_id == TEST_USER_ID:
            email, user_name = "test@test.com", "Test User"
        else:
            email, user_name = f"u-{uuid.uuid4().hex[:8]}@test.com", "Test User"
        user = User(id=user_id, email=email, name=user_name)
        db.add(user)
    model = await make_model(db)
    agent = await make_agent(
        db,
        user_id=user_id,
        model_id=model.id,
        name=name,
        system_prompt=system_prompt,
        **agent_kwargs,
    )
    return user, model, agent


# ---------------------------------------------------------------------------
# Auth session helper (BE-D7). Single register implementation for the
# ``raw_client`` cookie/CSRF flow — the per-file ``_register``/``_login``
# helpers in test_auth_*.py / test_csrf.py / test_multiuser_isolation.py
# are thin shims over this.
# ---------------------------------------------------------------------------


class AuthSession:
    """Cookie/CSRF bundle returned by ``register_session``."""

    def __init__(self, csrf: str, cookies: dict[str, str], body: dict):
        self.csrf = csrf
        self.cookies = cookies
        self.body = body

    def headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf}

    def apply(self, client) -> None:
        """Replace the client's cookies with this session's. Idempotent."""

        client.cookies.clear()
        for k, v in self.cookies.items():
            client.cookies.set(k, v)


async def register_session(
    client,
    *,
    email: str,
    password: str = "correct horse",
    name: str | None = None,
    display_name: str | None = None,
    super_first: bool = False,
    user_agent: str | None = None,
    clear_cookies: bool = False,
) -> AuthSession:
    """Register a fresh user via ``POST /api/auth/register`` (raw_client flow).

    ``super_first=True`` lets the first registered user become admin
    (``allow_first_user_as_admin``); the setting is restored afterwards.
    ``clear_cookies=True`` drops the cookies register set on the client so
    the next request starts clean (login-flow tests).
    """

    payload: dict[str, str] = {"email": email, "password": password}
    if name is not None:
        payload["name"] = name
    if display_name is not None:
        payload["display_name"] = display_name
    headers = {"user-agent": user_agent} if user_agent else None

    saved = settings.allow_first_user_as_admin
    settings.allow_first_user_as_admin = super_first
    try:
        resp = await client.post("/api/auth/register", json=payload, headers=headers)
    finally:
        settings.allow_first_user_as_admin = saved
    assert resp.status_code == 201, resp.text

    body = resp.json()
    cookies = {
        cookie_name: resp.cookies[cookie_name]
        for cookie_name in (
            settings.cookie_name_access,
            settings.cookie_name_refresh,
            settings.cookie_name_csrf,
        )
        if cookie_name in resp.cookies
    }
    if clear_cookies:
        client.cookies.clear()
    return AuthSession(csrf=body["csrf_token"], cookies=cookies, body=body)
