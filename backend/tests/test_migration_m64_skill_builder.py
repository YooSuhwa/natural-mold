from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import inspect

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "m64_skill_builder_sessions.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "m64_skill_builder_sessions_test_load",
        _MIGRATION,
    )
    assert spec is not None
    loader = spec.loader
    assert loader is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_module_imports_with_expected_revision_chain() -> None:
    mod = _load_module()

    assert mod.revision == "m64_skill_builder_sessions"
    assert mod.down_revision == "m63_chat_navigator_indexes"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


@pytest.mark.asyncio
async def test_upgrade_downgrade_roundtrip_sqlite() -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    from alembic import op as alembic_op

    mod = _load_module()
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.exec_driver_sql("CREATE TABLE users (id TEXT PRIMARY KEY)")
            conn.exec_driver_sql("CREATE TABLE skills (id TEXT PRIMARY KEY)")

            ctx = MigrationContext.configure(conn)
            alembic_op._proxy = Operations(ctx)

            mod.upgrade()
            inspector = inspect(conn)
            table_names = set(inspector.get_table_names())
            assert {
                "skill_builder_sessions",
                "skill_evaluation_sets",
                "skill_evaluation_runs",
                "skill_revisions",
            } <= table_names

            skill_columns = {column["name"] for column in inspector.get_columns("skills")}
            assert "current_revision_id" in skill_columns

            run_indexes = {
                index["name"] for index in inspector.get_indexes("skill_evaluation_runs")
            }
            assert "ix_skill_evaluation_runs_skill_created" in run_indexes
            assert "ix_skill_evaluation_runs_set_created" in run_indexes
            assert "ix_skill_evaluation_runs_status" in run_indexes

            mod.downgrade()
            inspector = inspect(conn)
            table_names = set(inspector.get_table_names())
            assert "skill_builder_sessions" not in table_names
            assert "skill_evaluation_sets" not in table_names
            assert "skill_evaluation_runs" not in table_names
            assert "skill_revisions" not in table_names
            skill_columns = {column["name"] for column in inspector.get_columns("skills")}
            assert "current_revision_id" not in skill_columns
    finally:
        engine.dispose()
