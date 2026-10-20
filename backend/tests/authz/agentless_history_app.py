from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.dependencies import get_db
from app.main import create_app
from app.services import audit_service
from tests.authz.organization_migration_fixture import async_organization_lab
from tests.authz.test_registration_policy_integration import SignupApp


@asynccontextmanager
async def agentless_history_app(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[SignupApp]:
    """Run the current app against an owned head-schema database with B08 lifecycle history."""
    async with async_organization_lab(at_head=True) as lab:
        engine = create_async_engine(lab.engine.url.set(drivername="postgresql+asyncpg"))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            app = create_app()

            async def database_session() -> AsyncGenerator[AsyncSession, None]:
                async with sessions() as db:
                    yield db

            app.dependency_overrides[get_db] = database_session
            monkeypatch.setattr(audit_service, "async_session", sessions)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield SignupApp(client, sessions)
        finally:
            await engine.dispose()
