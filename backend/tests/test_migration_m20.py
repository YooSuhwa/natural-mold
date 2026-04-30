"""m20 alembic migration — round-trip + metadata sanity tests.

The migration runs against the in-memory SQLite engine the rest of the suite
uses, so we don't need a Postgres container. The goal is to catch any
``upgrade`` / ``downgrade`` regression and confirm the ORM matches the
migration's column set.
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
    / "m20_add_health_check_history.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "m20_add_health_check_history_test_load", _MIGRATION
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_module_imports_with_expected_revision_chain() -> None:
    mod = _load_module()
    assert mod.revision == "m20_add_health_check_history"
    assert mod.down_revision == "m19_add_models_source"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


def test_metadata_includes_health_check_history_table() -> None:
    table = Base.metadata.tables.get("health_check_history")
    assert table is not None, "ORM metadata must include health_check_history"
    columns = set(table.columns.keys())
    expected = {
        "id",
        "target_kind",
        "target_id",
        "status",
        "latency_ms",
        "error_kind",
        "error_message",
        "raw_result",
        "checked_at",
    }
    assert expected.issubset(columns)
    indexes = {ix.name for ix in table.indexes}
    assert "ix_health_check_history_target_checked_at" in indexes


@pytest.mark.asyncio
async def test_upgrade_downgrade_roundtrip() -> None:
    """``upgrade`` → ``downgrade`` → ``upgrade`` against an in-memory DB."""

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    mod = _load_module()
    engine = create_engine("sqlite://")  # in-memory, sync
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            op = Operations(ctx)

            # alembic.op is a module-level proxy → patch its bind via a
            # context-manager scope so the migration sees our connection.
            from alembic import op as alembic_op

            alembic_op._proxy = op  # type: ignore[attr-defined]

            mod.upgrade()
            inspector = inspect(conn)
            assert "health_check_history" in inspector.get_table_names()

            mod.downgrade()
            inspector = inspect(conn)
            assert "health_check_history" not in inspector.get_table_names()

            mod.upgrade()
            inspector = inspect(conn)
            assert "health_check_history" in inspector.get_table_names()
    finally:
        engine.dispose()


def test_upgrade_idempotent_when_table_exists() -> None:
    """Re-running ``upgrade`` on an already-migrated DB is a no-op (no error)."""

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    mod = _load_module()
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conn:
            # Seed the table by hand so the helper's ``_has_table`` short-circuits.
            conn.exec_driver_sql(
                "CREATE TABLE health_check_history "
                "(id TEXT PRIMARY KEY, target_kind TEXT, target_id TEXT, "
                "status TEXT, latency_ms INTEGER, error_kind TEXT, "
                "error_message TEXT, raw_result TEXT, checked_at DATETIME)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX ix_health_check_history_target_checked_at "
                "ON health_check_history (target_kind, target_id, checked_at)"
            )
            ctx = MigrationContext.configure(conn)
            op = Operations(ctx)
            from alembic import op as alembic_op

            alembic_op._proxy = op  # type: ignore[attr-defined]

            mod.upgrade()
            inspector = inspect(conn)
            assert "health_check_history" in inspector.get_table_names()
    finally:
        engine.dispose()
