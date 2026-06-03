"""m54 alembic migration — SQLite batch_alter_table regression guard."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

_MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "m54_agent_identity.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("m54_agent_identity_test_load", _MIGRATION)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_module_imports_with_expected_revision_chain() -> None:
    mod = _load_module()
    assert mod.revision == "m54_agent_identity"
    assert mod.down_revision == "m53_performance_hot_path_indexes"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


@pytest.mark.asyncio
async def test_upgrade_downgrade_roundtrip_sqlite() -> None:
    """SQLite cannot run direct ALTER COLUMN or ADD CONSTRAINT operations."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text

    mod = _load_module()
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.exec_driver_sql("CREATE TABLE users (id TEXT PRIMARY KEY)")
            conn.exec_driver_sql(
                "CREATE TABLE agents ("
                "  id TEXT PRIMARY KEY,"
                "  user_id TEXT NOT NULL,"
                "  name TEXT NOT NULL"
                ")"
            )
            conn.exec_driver_sql(
                "CREATE TABLE agent_trigger_runs ("
                "  id TEXT PRIMARY KEY,"
                "  trigger_id TEXT,"
                "  agent_id TEXT"
                ")"
            )
            conn.exec_driver_sql(
                "INSERT INTO users (id) VALUES "
                "('00000000-0000-0000-0000-000000000001')"
            )
            conn.exec_driver_sql(
                "INSERT INTO agents (id, user_id, name) VALUES ("
                "  'aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb',"
                "  '00000000-0000-0000-0000-000000000001',"
                "  'Legacy Agent'"
                ")"
            )
            conn.commit()

            ctx = MigrationContext.configure(conn)
            op = Operations(ctx)
            from alembic import op as alembic_op

            alembic_op._proxy = op  # type: ignore[attr-defined]

            mod.upgrade()
            inspector = inspect(conn)
            agent_cols = {c["name"]: c for c in inspector.get_columns("agents")}
            assert agent_cols["runtime_name"]["nullable"] is False
            assert agent_cols["identity_mode"]["nullable"] is False
            run_cols = {c["name"] for c in inspector.get_columns("agent_trigger_runs")}
            assert "identity_mode" in run_cols
            assert "agent_runtime_name" in run_cols
            assert "credential_subject_user_id" in run_cols

            row = conn.execute(
                text(
                    "SELECT runtime_name, identity_mode FROM agents "
                    "WHERE id='aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb'"
                )
            ).one()
            assert row == ("agent_aaaaaaaa", "fixed")
            conn.commit()

            with pytest.raises(IntegrityError):
                conn.exec_driver_sql(
                    "INSERT INTO agents "
                    "(id, user_id, name, runtime_name, identity_mode) VALUES ("
                    "  'cccccccc-1111-2222-3333-dddddddddddd',"
                    "  '00000000-0000-0000-0000-000000000001',"
                    "  'Invalid Agent',"
                    "  'agent_cccccccc',"
                    "  'invalid'"
                    ")"
                )
            conn.rollback()

            mod.downgrade()
            inspector = inspect(conn)
            agent_cols = {c["name"] for c in inspector.get_columns("agents")}
            assert "runtime_name" not in agent_cols
            assert "identity_mode" not in agent_cols
            run_cols = {c["name"] for c in inspector.get_columns("agent_trigger_runs")}
            assert "identity_mode" not in run_cols
            assert "agent_runtime_name" not in run_cols
            assert "credential_subject_user_id" not in run_cols

            mod.upgrade()
            inspector = inspect(conn)
            agent_cols = {c["name"] for c in inspector.get_columns("agents")}
            assert "runtime_name" in agent_cols
            assert "identity_mode" in agent_cols
    finally:
        engine.dispose()
