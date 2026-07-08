"""m67(agents.runtime_profile) / m68(skill_builder_sessions v2) 마이그레이션 검증.

m64 테스트와 동일한 sqlite 라운드트립 방식 — 리비전 체인과 업/다운 그레이드가
실행 가능한지 가드한다 (실배포는 PostgreSQL, 여기선 구문/체인 회귀만).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import inspect

_VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def _load_module(filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"{filename.removesuffix('.py')}_test_load",
        _VERSIONS / filename,
    )
    assert spec is not None
    loader = spec.loader
    assert loader is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_m67_revision_chain() -> None:
    mod = _load_module("m68_agents_runtime_profile.py")
    assert mod.revision == "m68_agents_runtime_profile"
    assert mod.down_revision == "m67_hotpath_fk_indexes"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


def test_m68_revision_chain() -> None:
    mod = _load_module("m69_skill_builder_sessions_v2.py")
    assert mod.revision == "m69_skill_builder_sessions_v2"
    assert mod.down_revision == "m68_agents_runtime_profile"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


@pytest.mark.asyncio
async def test_m67_upgrade_downgrade_roundtrip_sqlite() -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    from alembic import op as alembic_op

    mod = _load_module("m68_agents_runtime_profile.py")
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("CREATE TABLE agents (id TEXT PRIMARY KEY, name TEXT)")
            conn.exec_driver_sql("INSERT INTO agents (id, name) VALUES ('a1', 'A')")

            ctx = MigrationContext.configure(conn)
            alembic_op._proxy = Operations(ctx)

            mod.upgrade()
            columns = {c["name"] for c in inspect(conn).get_columns("agents")}
            assert "runtime_profile" in columns
            # 기존 row는 server_default로 standard 백필.
            row = conn.exec_driver_sql(
                "SELECT runtime_profile FROM agents WHERE id='a1'"
            ).fetchone()
            assert row is not None and row[0] == "standard"

            mod.downgrade()
            columns = {c["name"] for c in inspect(conn).get_columns("agents")}
            assert "runtime_profile" not in columns
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_m68_upgrade_downgrade_roundtrip_sqlite() -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    from alembic import op as alembic_op

    mod = _load_module("m69_skill_builder_sessions_v2.py")
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("CREATE TABLE conversations (id TEXT PRIMARY KEY)")
            conn.exec_driver_sql("CREATE TABLE skill_builder_sessions (id TEXT PRIMARY KEY)")

            ctx = MigrationContext.configure(conn)
            alembic_op._proxy = Operations(ctx)

            mod.upgrade()
            inspector = inspect(conn)
            columns = {c["name"] for c in inspector.get_columns("skill_builder_sessions")}
            assert {
                "conversation_id",
                "draft_workspace_path",
                "tool_consents",
            } <= columns
            indexes = {i["name"] for i in inspector.get_indexes("skill_builder_sessions")}
            assert "ix_skill_builder_sessions_conversation" in indexes

            mod.downgrade()
            inspector = inspect(conn)
            columns = {c["name"] for c in inspector.get_columns("skill_builder_sessions")}
            assert "conversation_id" not in columns
            assert "draft_workspace_path" not in columns
            assert "tool_consents" not in columns
    finally:
        engine.dispose()
