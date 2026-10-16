"""B-13: tenant/platform session factories and the per-transaction scope listener.

The production listener only speaks to PostgreSQL. Here a real SQLAlchemy
session over aiosqlite gets a recording ``set_config`` SQL function and the
dialect gate is opened, so the actual ``after_begin`` plumbing is exercised:
each new transaction (including the one after a commit) re-applies the scope.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app import database
from app.config import settings
from app.database import (
    SessionScopeError,
    SessionTenant,
    bind_tenant,
    platform_session,
    platform_sessions,
    tenant_session,
    tenant_sessions,
)

TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
OTHER = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
USER = uuid.UUID("00000000-0000-0000-0000-0000000000c3")


@pytest.fixture
async def recording_engine() -> AsyncIterator[tuple[AsyncEngine, list[tuple[str, str, int]]]]:
    calls: list[tuple[str, str, int]] = []
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    def _register(dbapi_connection: object, _record: object) -> None:
        def set_config(name: str, value: str, is_local: int) -> str:
            calls.append((name, value, is_local))
            return value

        dbapi_connection.create_function("set_config", 3, set_config)  # type: ignore[attr-defined]

    event.listen(engine.sync_engine, "connect", _register)
    yield engine, calls
    await engine.dispose()


def _open_gate(monkeypatch: pytest.MonkeyPatch, *, rls: bool = True) -> None:
    """Pretend the aiosqlite connection is PostgreSQL; RLS follows the setting."""
    monkeypatch.setattr(database, "_is_postgres_connection", lambda _connection: True)
    monkeypatch.setattr(settings, "db_rls_enabled", rls)


async def test_rls_disabled_issues_no_settings_even_on_postgres(
    recording_engine: tuple[AsyncEngine, list[tuple[str, str, int]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, calls = recording_engine
    _open_gate(monkeypatch, rls=False)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with tenant_session(SessionTenant(TENANT, USER), maker=maker) as db:
        await db.execute(text("SELECT 1"))
        await db.commit()
        await db.execute(text("SELECT 1"))
    async with tenant_session(None, maker=maker) as db:
        await db.execute(text("SELECT 1"))
        await bind_tenant(db, SessionTenant(TENANT, USER))
    assert calls == []


async def test_unbound_transaction_is_counted_when_rls_is_enabled(
    recording_engine: tuple[AsyncEngine, list[tuple[str, str, int]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, calls = recording_engine
    _open_gate(monkeypatch)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    before = database.unbound_transaction_count()
    async with tenant_session(None, maker=maker) as db:
        await db.execute(text("SELECT 1"))
        await db.commit()
        await db.execute(text("SELECT 1"))
    assert database.unbound_transaction_count() == before + 2
    assert calls == []


async def test_tenant_session_reapplies_scope_on_every_transaction(
    recording_engine: tuple[AsyncEngine, list[tuple[str, str, int]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, calls = recording_engine
    _open_gate(monkeypatch)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with tenant_session(SessionTenant(TENANT, USER), maker=maker) as db:
        await db.execute(text("SELECT 1"))
        await db.commit()
        await db.execute(text("SELECT 1"))
        await db.rollback()
        await db.execute(text("SELECT 1"))

    tenant_calls = [call for call in calls if call[0] == "app.tenant_id"]
    assert tenant_calls == [("app.tenant_id", str(TENANT), 1)] * 3
    assert ("app.user_id", str(USER), 1) in calls
    assert all(is_local == 1 for _, _, is_local in calls)


async def test_platform_session_sets_platform_scope_only(
    recording_engine: tuple[AsyncEngine, list[tuple[str, str, int]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, calls = recording_engine
    _open_gate(monkeypatch)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with platform_session("seed.startup", maker=maker) as db:
        await db.execute(text("SELECT 1"))
        assert db.info["moldy_db_reason"] == "seed.startup"

    assert calls == [("app.scope", "platform", 1)]


async def test_unbound_session_sets_nothing_until_bound_then_applies_immediately(
    recording_engine: tuple[AsyncEngine, list[tuple[str, str, int]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, calls = recording_engine
    _open_gate(monkeypatch)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with tenant_session(None, maker=maker) as db:
        await db.execute(text("SELECT 1"))
        assert calls == []
        await bind_tenant(db, SessionTenant(TENANT, USER))
        assert ("app.tenant_id", str(TENANT), 1) in calls
        await db.commit()
        calls.clear()
        await db.execute(text("SELECT 1"))
        assert ("app.tenant_id", str(TENANT), 1) in calls


async def test_bind_tenant_refuses_a_second_company(
    recording_engine: tuple[AsyncEngine, list[tuple[str, str, int]]],
) -> None:
    engine, _calls = recording_engine
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with tenant_session(SessionTenant(TENANT, USER), maker=maker) as db:
        await bind_tenant(db, SessionTenant(TENANT, USER))  # identical scope is idempotent
        with pytest.raises(SessionScopeError, match="already bound"):
            await bind_tenant(db, SessionTenant(OTHER, USER))
        with pytest.raises(SessionScopeError, match="already bound"):
            await bind_tenant(db, SessionTenant(TENANT, OTHER))
        with pytest.raises(SessionScopeError, match="already bound"):
            await bind_tenant(db, SessionTenant(TENANT))
    async with platform_session("seed.startup", maker=maker) as db:
        with pytest.raises(SessionScopeError, match="already bound"):
            await bind_tenant(db, SessionTenant(TENANT))


async def test_bind_tenant_refuses_inside_a_nested_transaction(
    recording_engine: tuple[AsyncEngine, list[tuple[str, str, int]]],
) -> None:
    engine, _calls = recording_engine
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with tenant_session(None, maker=maker) as db:
        await db.execute(text("SELECT 1"))
        async with db.begin_nested():
            with pytest.raises(SessionScopeError, match="nested"):
                await bind_tenant(db, SessionTenant(TENANT, USER))
        assert db.info.get("moldy_db_scope") is None


async def test_sqlite_dialect_is_a_no_op_even_with_rls_enabled(
    recording_engine: tuple[AsyncEngine, list[tuple[str, str, int]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, calls = recording_engine
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with tenant_session(SessionTenant(TENANT, USER), maker=maker) as db:
        await db.execute(text("SELECT 1"))
        await bind_tenant(db, SessionTenant(TENANT, USER))
    assert calls == []


async def test_factories_read_the_default_maker_at_call_time(
    recording_engine: tuple[AsyncEngine, list[tuple[str, str, int]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, _calls = recording_engine
    maker = async_sessionmaker(engine, expire_on_commit=False)
    tenant_factory = tenant_sessions(SessionTenant(TENANT))
    platform_factory = platform_sessions("seed.startup")
    monkeypatch.setattr(database, "async_session", maker)

    async with tenant_factory() as db:
        assert db.bind is engine
        assert db.info["moldy_db_scope"] == {"app.tenant_id": str(TENANT)}
    async with platform_factory() as db:
        assert db.bind is engine
        assert db.info["moldy_db_scope"] == {"app.scope": "platform"}


async def test_default_factory_is_the_shielded_application_sessionmaker() -> None:
    session = tenant_session(SessionTenant(TENANT))
    try:
        assert isinstance(session, database.ShieldedAsyncSession)
        assert session.info["moldy_db_scope"] == {"app.tenant_id": str(TENANT)}
    finally:
        await session.close()
