"""M61 conversation_runs migration — durable chat run lifecycle table."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.database import Base

_MIGRATION = (
    Path(__file__).resolve().parent.parent / "alembic" / "versions" / "m61_conversation_runs.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("m61_conversation_runs_test_load", _MIGRATION)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_module_imports_with_expected_revision_chain() -> None:
    mod = _load_module()
    assert mod.revision == "m61_conversation_runs"
    assert mod.down_revision == "m60_credential_oauth_states"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


def test_metadata_includes_conversation_runs_table() -> None:
    table = Base.metadata.tables.get("conversation_runs")
    assert table is not None
    assert {
        "id",
        "conversation_id",
        "agent_id",
        "user_id",
        "parent_run_id",
        "source",
        "status",
        "is_active",
        "worker_instance_id",
        "interrupt_id",
        "input_preview",
        "last_event_id",
        "error_code",
        "error_message",
        "cancel_requested_at",
        "started_at",
        "heartbeat_at",
        "completed_at",
        "created_at",
        "updated_at",
        "metadata_json",
    } <= set(table.columns.keys())
    assert "uq_conversation_runs_active_conversation" in {index.name for index in table.indexes}


@pytest.mark.asyncio
async def test_upgrade_downgrade_roundtrip_sqlite() -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    mod = _load_module()
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.exec_driver_sql("CREATE TABLE users (id TEXT PRIMARY KEY)")
            conn.exec_driver_sql("CREATE TABLE agents (id TEXT PRIMARY KEY)")
            conn.exec_driver_sql("CREATE TABLE conversations (id TEXT PRIMARY KEY)")

            ctx = MigrationContext.configure(conn)
            op = Operations(ctx)
            from alembic import op as alembic_op

            alembic_op._proxy = op  # type: ignore[attr-defined]

            mod.upgrade()
            inspector = inspect(conn)
            cols = {c["name"] for c in inspector.get_columns("conversation_runs")}
            assert "worker_instance_id" in cols
            assert "interrupt_id" in cols
            indexes = {ix["name"] for ix in inspector.get_indexes("conversation_runs")}
            assert "ix_conversation_runs_conversation_created" in indexes
            assert "uq_conversation_runs_active_conversation" in indexes

            mod.downgrade()
            inspector = inspect(conn)
            assert "conversation_runs" not in inspector.get_table_names()

            mod.upgrade()
            inspector = inspect(conn)
            assert "conversation_runs" in inspector.get_table_names()
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_upgrade_enforces_single_active_run_per_conversation_sqlite() -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    mod = _load_module()
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.exec_driver_sql("CREATE TABLE users (id TEXT PRIMARY KEY)")
            conn.exec_driver_sql("CREATE TABLE agents (id TEXT PRIMARY KEY)")
            conn.exec_driver_sql("CREATE TABLE conversations (id TEXT PRIMARY KEY)")
            conn.exec_driver_sql(
                "INSERT INTO users (id) VALUES ('00000000-0000-0000-0000-000000000001')"
            )
            conn.exec_driver_sql(
                "INSERT INTO agents (id) VALUES ('aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb')"
            )
            conn.exec_driver_sql(
                "INSERT INTO conversations (id) VALUES ('cccccccc-1111-2222-3333-dddddddddddd')"
            )

            ctx = MigrationContext.configure(conn)
            op = Operations(ctx)
            from alembic import op as alembic_op

            alembic_op._proxy = op  # type: ignore[attr-defined]
            mod.upgrade()

            conn.exec_driver_sql(
                "INSERT INTO conversation_runs "
                "(id, conversation_id, agent_id, user_id, source, status, is_active) VALUES "
                "('11111111-1111-1111-1111-111111111111',"
                " 'cccccccc-1111-2222-3333-dddddddddddd',"
                " 'aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb',"
                " '00000000-0000-0000-0000-000000000001',"
                " 'chat', 'running', 1)"
            )
            with pytest.raises(IntegrityError):
                conn.exec_driver_sql(
                    "INSERT INTO conversation_runs "
                    "(id, conversation_id, agent_id, user_id, source, status, is_active) VALUES "
                    "('22222222-2222-2222-2222-222222222222',"
                    " 'cccccccc-1111-2222-3333-dddddddddddd',"
                    " 'aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb',"
                    " '00000000-0000-0000-0000-000000000001',"
                    " 'chat', 'queued', 1)"
                )
    finally:
        engine.dispose()
