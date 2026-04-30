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
from app.dependencies import CurrentUser, get_current_user, get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.security import key_provider  # noqa: E402

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
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSession() as session:
        yield session


async def override_get_current_user() -> CurrentUser:
    return CurrentUser(id=TEST_USER_ID, email="test@test.com", name="Test User")


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSession() as session:
        yield session
