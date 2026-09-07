from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = BACKEND_ROOT / "alembic" / "versions" / "m76_pinned_conv_summaries.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("m76_pinned_summary_test", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m76_is_linear_and_enforces_bounded_conversation_owned_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from alembic import op as alembic_op

    migration = _load_migration()
    engine = create_engine("sqlite://")
    conversation_id = "ownerconversation00000000000000000"
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        connection.exec_driver_sql("CREATE TABLE conversations (id CHAR(32) PRIMARY KEY)")
        connection.exec_driver_sql(
            "INSERT INTO conversations VALUES (?)",
            (conversation_id,),
        )
        context = MigrationContext.configure(connection)
        monkeypatch.setattr(alembic_op, "_proxy", Operations(context), raising=False)

        migration.upgrade()

        assert migration.revision == "m76_pinned_conv_summaries"
        assert len(migration.revision) <= 32
        assert migration.down_revision == "m75_mcp_apps_provenance"
        table = "conversation_pinned_summaries"
        assert inspect(connection).get_pk_constraint(table)["constrained_columns"] == [
            "conversation_id"
        ]
        foreign_keys = inspect(connection).get_foreign_keys(table)
        assert foreign_keys[0]["referred_table"] == "conversations"
        assert foreign_keys[0].get("options") == {"ondelete": "CASCADE"}

        values = (
            conversation_id,
            "assistant-1",
            "checkpoint-1",
            "x" * 4001,
            "0" * 64,
            "2026-09-06 00:00:00",
            "2026-09-06 00:00:00",
        )
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(
                "INSERT INTO conversation_pinned_summaries VALUES (?, ?, ?, ?, ?, ?, ?)",
                values,
            )

        migration.downgrade()
        assert table not in inspect(connection).get_table_names()
    engine.dispose()
