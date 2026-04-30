"""m21 alembic migration — round-trip + metadata sanity tests.

Mirrors :mod:`tests.test_migration_m20` so the in-memory SQLite engine is
enough to catch regressions; a Postgres round-trip is verified manually
(see CHECKPOINT.md).
"""

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
    / "m21_add_daily_spend_aggregates.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "m21_add_daily_spend_aggregates_test_load", _MIGRATION
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_module_imports_with_expected_revision_chain() -> None:
    mod = _load_module()
    assert mod.revision == "m21_add_daily_spend_aggregates"
    assert mod.down_revision == "m20_add_health_check_history"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


def test_metadata_includes_three_aggregate_tables() -> None:
    for table_name in ("daily_spend_user", "daily_spend_agent", "daily_spend_model"):
        table = Base.metadata.tables.get(table_name)
        assert table is not None, f"ORM metadata must include {table_name}"
        columns = set(table.columns.keys())
        for required in (
            "id",
            "date",
            "total_tokens_in",
            "total_tokens_out",
            "total_cost_usd",
            "request_count",
            "updated_at",
        ):
            assert required in columns, f"{table_name} missing column {required}"


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
            # The aggregate tables FK to users / agents / models — bootstrap
            # those so foreign-key DDL succeeds even on SQLite.
            conn.exec_driver_sql(
                "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT, name TEXT)"
            )
            conn.exec_driver_sql(
                "CREATE TABLE models (id TEXT PRIMARY KEY, provider TEXT, "
                "model_name TEXT, display_name TEXT)"
            )
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
            for table_name in ("daily_spend_user", "daily_spend_agent", "daily_spend_model"):
                assert table_name in inspector.get_table_names()

            mod.downgrade()
            inspector = inspect(conn)
            for table_name in ("daily_spend_user", "daily_spend_agent", "daily_spend_model"):
                assert table_name not in inspector.get_table_names()

            mod.upgrade()
            inspector = inspect(conn)
            for table_name in ("daily_spend_user", "daily_spend_agent", "daily_spend_model"):
                assert table_name in inspector.get_table_names()
    finally:
        engine.dispose()


def test_upgrade_idempotent_when_table_exists() -> None:
    """Re-running ``upgrade`` on already-migrated tables is a no-op."""

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    mod = _load_module()
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT, name TEXT)"
            )
            conn.exec_driver_sql(
                "CREATE TABLE models (id TEXT PRIMARY KEY, provider TEXT, "
                "model_name TEXT, display_name TEXT)"
            )
            conn.exec_driver_sql(
                "CREATE TABLE agents (id TEXT PRIMARY KEY, user_id TEXT, "
                "name TEXT, system_prompt TEXT, model_id TEXT, status TEXT)"
            )
            # Pre-create one of the aggregate tables to exercise the
            # ``_has_table`` short-circuit.
            conn.exec_driver_sql(
                "CREATE TABLE daily_spend_user "
                "(id TEXT PRIMARY KEY, date DATE, user_id TEXT, "
                "total_tokens_in INTEGER, total_tokens_out INTEGER, "
                "total_cost_usd NUMERIC, request_count INTEGER, updated_at DATETIME)"
            )
            ctx = MigrationContext.configure(conn)
            op = Operations(ctx)
            from alembic import op as alembic_op

            alembic_op._proxy = op  # type: ignore[attr-defined]

            mod.upgrade()
            inspector = inspect(conn)
            for table_name in ("daily_spend_user", "daily_spend_agent", "daily_spend_model"):
                assert table_name in inspector.get_table_names()
    finally:
        engine.dispose()
