"""B-13 on real PostgreSQL: scope survives commits and never leaks across sessions.

Runs only in the disposable PostgreSQL lane (``-m integration``) with
``DB_RLS_ENABLED`` on. No RLS policy is installed here (I-06/m92); the
transaction-local settings that the policies will read are asserted directly.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import database
from app.config import settings
from app.database import SessionTenant, bind_tenant, platform_session, tenant_session

TENANT = uuid.UUID("00000000-0000-0000-0000-00000000b013")
USER = uuid.UUID("00000000-0000-0000-0000-00000000c013")
_SETTINGS = (
    "SELECT current_setting('app.tenant_id', true), current_setting('app.user_id', true), "
    "current_setting('app.scope', true)"
)


@pytest.fixture(autouse=True)
def rls_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # Settings are only issued when the RLS flag is on (default install: off).
    monkeypatch.setattr(settings, "db_rls_enabled", True)


@pytest.fixture
async def one_connection() -> AsyncIterator[async_sessionmaker[database.ShieldedAsyncSession]]:
    # A single pooled connection proves values do not survive into the next user.
    engine = create_async_engine(settings.database_url, pool_size=1, max_overflow=0)
    try:
        yield async_sessionmaker(engine, class_=database.ShieldedAsyncSession)
    finally:
        await engine.dispose()


async def _settings(db: AsyncSession) -> tuple[str | None, str | None, str | None]:
    row = (await db.execute(text(_SETTINGS))).one()
    return row[0] or None, row[1] or None, row[2] or None


async def test_scope_is_reapplied_after_commit_and_rollback(
    one_connection: async_sessionmaker[database.ShieldedAsyncSession],
) -> None:
    expected = (str(TENANT), str(USER), None)
    async with tenant_session(SessionTenant(TENANT, USER), maker=one_connection) as db:
        assert await _settings(db) == expected
        await db.commit()
        assert await _settings(db) == expected
        await db.rollback()
        assert await _settings(db) == expected
        async with db.begin_nested():
            assert await _settings(db) == expected


async def test_values_do_not_leak_to_the_next_session_on_the_same_connection(
    one_connection: async_sessionmaker[database.ShieldedAsyncSession],
) -> None:
    async with tenant_session(SessionTenant(TENANT, USER), maker=one_connection) as db:
        assert (await _settings(db))[0] == str(TENANT)
        await db.commit()
    async with tenant_session(None, maker=one_connection) as db:
        assert await _settings(db) == (None, None, None)
    async with platform_session("seed.startup", maker=one_connection) as db:
        assert await _settings(db) == (None, None, "platform")


async def test_bound_request_session_applies_now_and_after_commit(
    one_connection: async_sessionmaker[database.ShieldedAsyncSession],
) -> None:
    async with tenant_session(None, maker=one_connection) as db:
        assert await _settings(db) == (None, None, None)
        await bind_tenant(db, SessionTenant(TENANT, USER))
        assert await _settings(db) == (str(TENANT), str(USER), None)
        await db.commit()
        assert await _settings(db) == (str(TENANT), str(USER), None)
