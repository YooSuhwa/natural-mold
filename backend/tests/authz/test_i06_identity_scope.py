"""I-06: the identity gateway scope for lookups that run before a company is bound.

A real SQLAlchemy session over aiosqlite records ``set_config`` calls with the
PostgreSQL gate opened (as in ``tests/test_db_session_scope.py``).
"""

from __future__ import annotations

import ast
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app import database
from app.config import settings
from app.database import (
    SCOPE_INFO_KEY,
    SessionScopeError,
    SessionTenant,
    bind_identity,
    bind_tenant,
    identity_scope,
    tenant_session,
)

TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
USER = uuid.UUID("00000000-0000-0000-0000-0000000000c3")
APP = Path(__file__).resolve().parents[2] / "app"
# Every place that may open the identity scope (account gateway only).
IDENTITY_SITES = {
    ("dependencies.py", "identity_scope"): 2,  # token user, org-context resolution
    ("dependencies.py", "bind_identity"): 1,  # get_identity_db
    ("routers/auth.py", "get_identity_db"): 1,
    ("routers/auth_sso.py", "get_identity_db"): 1,
}
Calls = list[tuple[str, str, int]]


@pytest.fixture
async def recorded(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[AsyncEngine, Calls]]:
    calls: Calls = []
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    def _register(dbapi_connection: object, _record: object) -> None:
        def set_config(name: str, value: str, is_local: int) -> str:
            calls.append((name, value, is_local))
            return value

        dbapi_connection.create_function("set_config", 3, set_config)  # type: ignore[attr-defined]

    event.listen(engine.sync_engine, "connect", _register)
    monkeypatch.setattr(database, "_is_postgres_connection", lambda _connection: True)
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    yield engine, calls
    await engine.dispose()


def _scope(calls: Calls) -> list[str]:
    return [value for name, value, _local in calls if name == "app.scope"]


async def test_identity_scope_applies_now_and_clears_before_the_tenant_binding(
    recorded: tuple[AsyncEngine, Calls],
) -> None:
    engine, calls = recorded
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with tenant_session(None, maker=maker) as db:
        await db.execute(text("SELECT 1"))  # unbound transaction already open
        async with identity_scope(db, USER):
            assert db.info[SCOPE_INFO_KEY] == {"app.scope": "identity", "app.user_id": str(USER)}
            await db.execute(text("SELECT 1"))
        assert SCOPE_INFO_KEY not in db.info
        await bind_tenant(db, SessionTenant(TENANT, USER))
    assert _scope(calls) == ["identity", ""]
    assert ("app.tenant_id", str(TENANT), 1) in calls
    assert all(local == 1 for _name, _value, local in calls)


async def test_identity_scope_is_cleared_when_the_lookup_fails(
    recorded: tuple[AsyncEngine, Calls],
) -> None:
    engine, calls = recorded
    async with tenant_session(None, maker=async_sessionmaker(engine)) as db:

        async def lookup() -> None:
            async with identity_scope(db):
                await db.execute(text("SELECT 1"))
                raise LookupError

        with pytest.raises(LookupError):
            await lookup()
        assert SCOPE_INFO_KEY not in db.info
    assert _scope(calls) == ["identity", ""]


async def test_identity_scope_leaves_a_bound_session_alone(
    recorded: tuple[AsyncEngine, Calls],
) -> None:
    engine, calls = recorded
    async with tenant_session(SessionTenant(TENANT, USER), maker=async_sessionmaker(engine)) as db:
        async with identity_scope(db, USER):
            await db.execute(text("SELECT 1"))
        assert db.info[SCOPE_INFO_KEY]["app.tenant_id"] == str(TENANT)
    assert _scope(calls) == []


async def test_bound_identity_survives_commits_and_never_becomes_a_company(
    recorded: tuple[AsyncEngine, Calls],
) -> None:
    engine, calls = recorded
    async with tenant_session(None, maker=async_sessionmaker(engine)) as db:
        await bind_identity(db)
        await db.execute(text("SELECT 1"))
        await db.commit()
        await db.execute(text("SELECT 1"))
        await bind_identity(db)  # idempotent
        with pytest.raises(SessionScopeError):
            await bind_tenant(db, SessionTenant(TENANT, USER))
    assert _scope(calls) == ["identity", "identity"]
    async with tenant_session(SessionTenant(TENANT), maker=async_sessionmaker(engine)) as db:
        with pytest.raises(SessionScopeError):
            await bind_identity(db)


def _sites() -> dict[tuple[str, str], int]:
    names = {"identity_scope", "bind_identity", "get_identity_db"}
    found: dict[tuple[str, str], int] = {}
    for path in sorted(APP.rglob("*.py")):
        if path.name == "database.py" and path.parent == APP:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            name = None
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name == "Depends" and node.args and isinstance(node.args[0], ast.Name):
                    name = node.args[0].id
            if name in names and not (name == "get_identity_db" and path.name == "dependencies.py"):
                key = (path.relative_to(APP).as_posix(), name)
                found[key] = found.get(key, 0) + 1
    return found


def test_identity_scope_is_only_opened_by_the_account_gateway() -> None:
    assert _sites() == IDENTITY_SITES
