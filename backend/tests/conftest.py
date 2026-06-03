from __future__ import annotations

import os
import secrets
import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Cipher V2 / new credential domain require ``ENCRYPTION_KEYS`` to be set
# before ``app.config.settings`` is evaluated. Set it at import time so any
# module that reads settings during collection sees a valid value.
os.environ.setdefault("ENCRYPTION_KEYS", secrets.token_hex(32))

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

# Make sure the settings instance reflects the environment variable above
# (``BaseSettings`` reads env at class-init time, but explicit assignment
# guarantees correctness if any other test mutated ``settings``).
if not getattr(settings, "encryption_keys", ""):
    settings.encryption_keys = os.environ["ENCRYPTION_KEYS"]
key_provider.reset_cache()

TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

engine = create_async_engine("sqlite+aiosqlite://", echo=False)
TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Snapshot cache leaks across tests otherwise — clear at boundary.
    share_cache.clear_all()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


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
        "app.routers.conversations.resolve_llm_api_key_for_agent",
        _fake_resolve,
    )
    monkeypatch.setattr(
        "app.agent_runtime.trigger_executor.resolve_llm_api_key_for_agent",
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

    return None


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_current_user_optional] = override_get_current_user
    app.dependency_overrides[verify_csrf] = _bypass_verify_csrf

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def raw_client() -> AsyncGenerator[AsyncClient, None]:
    """App without auth/CSRF overrides — exercises real cookie + JWT flow.

    Used by /api/auth tests and the multi-user isolation matrix.
    """

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
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
