"""Private SQLite fixture with immediate foreign-key enforcement."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.auth.jwt import create_access_token, create_csrf_token
from app.config import settings
from app.database import Base
from app.dependencies import get_db
from app.main import create_app
from app.models.user import User
from app.services import audit_service

FK_ACTOR_ID = UUID("23232323-2323-4323-8323-232323232323")


@dataclass(frozen=True, slots=True)
class ForeignKeyTenantCreationCase:
    client: AsyncClient
    observer: async_sessionmaker[AsyncSession]
    actor_id: UUID
    csrf: str

    def headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf}


def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA foreign_keys")
        assert cursor.fetchone() == (1,)
    finally:
        cursor.close()


def _engine(path: Path) -> AsyncEngine:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", echo=False)
    event.listen(engine.sync_engine, "connect", _enable_foreign_keys)
    return engine


@pytest.fixture
async def fk_tenant_creation_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[ForeignKeyTenantCreationCase]:
    database = tmp_path / "task12-fk.sqlite3"
    primary_engine = _engine(database)
    observer_engine = _engine(database)
    primary = async_sessionmaker(primary_engine, class_=AsyncSession, expire_on_commit=False)
    observer = async_sessionmaker(observer_engine, class_=AsyncSession, expire_on_commit=False)
    app: FastAPI | None = None
    try:
        async with observer_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with observer() as db:
            db.add(
                User(
                    id=FK_ACTOR_ID,
                    email="task12-fk-operator@example.com",
                    name="Task 12 FK Operator",
                    hashed_password="unused",
                    is_active=True,
                    is_super_user=True,
                )
            )
            await db.commit()

        monkeypatch.setattr(settings, "deployment_mode", "multi_tenant")
        monkeypatch.setattr(audit_service, "async_session", observer)
        assert settings.deployment_mode == "multi_tenant"
        app = create_app()

        async def database_session() -> AsyncGenerator[AsyncSession, None]:
            async with primary() as db:
                yield db

        app.dependency_overrides[get_db] = database_session
        csrf = create_csrf_token(FK_ACTOR_ID)
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://fk-test",
        ) as client:
            client.cookies.set(
                settings.cookie_name_access,
                create_access_token(FK_ACTOR_ID, is_super_user=True),
            )
            client.cookies.set(settings.cookie_name_csrf, csrf)
            yield ForeignKeyTenantCreationCase(client, observer, FK_ACTOR_ID, csrf)
            assert ACTIVE_ORG_COOKIE not in client.cookies
    finally:
        if app is not None:
            app.dependency_overrides.clear()
        await primary_engine.dispose()
        await observer_engine.dispose()
