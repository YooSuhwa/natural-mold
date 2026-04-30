"""m22 alembic migration — round-trip + metadata sanity tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import inspect

from app.database import Base

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "m22_add_agent_model_fallback.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "m22_add_agent_model_fallback_test_load", _MIGRATION
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_module_imports_with_expected_revision_chain() -> None:
    mod = _load_module()
    assert mod.revision == "m22_add_agent_model_fallback"
    assert mod.down_revision == "m21_add_daily_spend_aggregates"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


def test_metadata_includes_model_fallback_list_column() -> None:
    table = Base.metadata.tables.get("agents")
    assert table is not None
    assert "model_fallback_list" in table.columns


@pytest.mark.asyncio
async def test_upgrade_downgrade_roundtrip() -> None:
    """``upgrade`` → ``downgrade`` → ``upgrade`` against in-memory SQLite."""

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    mod = _load_module()
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE agents (id TEXT PRIMARY KEY, user_id TEXT, "
                "name TEXT, system_prompt TEXT, model_id TEXT, status TEXT)"
            )
            ctx = MigrationContext.configure(conn)
            op = Operations(ctx)
            from alembic import op as alembic_op

            alembic_op._proxy = op  # type: ignore[attr-defined]

            mod.upgrade()
            inspector = inspect(conn)
            cols = {c["name"] for c in inspector.get_columns("agents")}
            assert "model_fallback_list" in cols

            mod.downgrade()
            inspector = inspect(conn)
            cols = {c["name"] for c in inspector.get_columns("agents")}
            assert "model_fallback_list" not in cols

            mod.upgrade()
            inspector = inspect(conn)
            cols = {c["name"] for c in inspector.get_columns("agents")}
            assert "model_fallback_list" in cols
    finally:
        engine.dispose()


def test_upgrade_idempotent_when_column_exists() -> None:
    """Running upgrade twice doesn't fail; the helper short-circuits."""

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    mod = _load_module()
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE agents (id TEXT PRIMARY KEY, user_id TEXT, "
                "name TEXT, system_prompt TEXT, model_id TEXT, status TEXT, "
                "model_fallback_list JSON)"
            )
            ctx = MigrationContext.configure(conn)
            op = Operations(ctx)
            from alembic import op as alembic_op

            alembic_op._proxy = op  # type: ignore[attr-defined]

            mod.upgrade()
            inspector = inspect(conn)
            cols = {c["name"] for c in inspector.get_columns("agents")}
            assert "model_fallback_list" in cols
    finally:
        engine.dispose()
