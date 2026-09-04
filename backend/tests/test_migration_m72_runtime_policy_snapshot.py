from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import create_engine, inspect

from app.agent_runtime.runtime_policy import LEGACY_RUNTIME_POLICY, runtime_policy_to_json

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = BACKEND_ROOT / "alembic" / "versions" / "m72_runtime_policy_snapshot.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "m72_runtime_policy_snapshot_test",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m72_revision_chain_and_default_literal_match_runtime_resolver() -> None:
    # Given the isolated migration-local compatibility literal.
    migration = _load_migration()

    # When migration metadata and policy bytes are compared with the runtime contract.
    migration_json = json.dumps(migration._DEFAULT_POLICY, separators=(",", ":"), sort_keys=True)

    # Then M72 is linear and backfills the exact resolver-owned canonical policy.
    assert migration.revision == "m72_runtime_policy_snapshot"
    assert migration.down_revision == "m71_runtime_policy"
    assert runtime_policy_to_json(LEGACY_RUNTIME_POLICY.effective) == migration._DEFAULT_POLICY
    assert migration_json == LEGACY_RUNTIME_POLICY.canonical_json
    assert LEGACY_RUNTIME_POLICY.policy_hash == migration._DEFAULT_HASH


def test_m72_backfills_existing_rows_keeps_new_rows_nullable_and_downgrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given legacy conversations/runs for standard and Skill Builder agents.
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from alembic import op as alembic_op

    migration = _load_migration()
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE agents (id TEXT PRIMARY KEY, runtime_profile TEXT NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE conversations (id TEXT PRIMARY KEY, agent_id TEXT NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE conversation_runs (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO agents VALUES ('standard', 'standard'), ('builder', 'skill_builder')"
        )
        connection.exec_driver_sql(
            "INSERT INTO conversations VALUES ('c1', 'standard'), ('c2', 'builder')"
        )
        connection.exec_driver_sql(
            "INSERT INTO conversation_runs VALUES ('r1', 'c1'), ('r2', 'c2')"
        )
        context = MigrationContext.configure(connection)
        monkeypatch.setattr(alembic_op, "_proxy", Operations(context), raising=False)

        # When M72 upgrades the legacy schema.
        migration.upgrade()

        # Then existing rows and runs receive exact source/hash/version tuples.
        conversation_rows = connection.exec_driver_sql(
            "SELECT id, runtime_policy_version, runtime_policy_hash, runtime_policy_source "
            "FROM conversations ORDER BY id"
        ).all()
        assert conversation_rows == [
            ("c1", 1, migration._DEFAULT_HASH, "legacy_compat"),
            ("c2", 1, migration._DEFAULT_HASH, "server_owned"),
        ]
        run_rows = connection.exec_driver_sql(
            "SELECT id, runtime_policy_version, runtime_policy_hash, runtime_policy_source "
            "FROM conversation_runs ORDER BY id"
        ).all()
        assert run_rows == [
            ("r1", 1, migration._DEFAULT_HASH, "legacy_compat"),
            ("r2", 1, migration._DEFAULT_HASH, "server_owned"),
        ]

        # When old code inserts a new row without policy columns.
        connection.exec_driver_sql(
            "INSERT INTO conversations (id, agent_id) VALUES ('c3', 'standard')"
        )

        # Then additive fields remain nullable and have no server defaults.
        assert connection.exec_driver_sql(
            "SELECT runtime_policy_snapshot, runtime_policy_version, runtime_policy_hash, "
            "runtime_policy_source FROM conversations WHERE id='c3'"
        ).one() == (None, None, None, None)
        columns = {
            column["name"]: column for column in inspect(connection).get_columns("conversations")
        }
        assert columns["runtime_policy_snapshot"]["default"] is None
        assert columns["runtime_policy_version"]["default"] is None

        # When M72 is downgraded.
        migration.downgrade()

        # Then only its additive fields are removed.
        assert "runtime_policy_snapshot" not in {
            column["name"] for column in inspect(connection).get_columns("conversations")
        }
        assert "runtime_policy_version" not in {
            column["name"] for column in inspect(connection).get_columns("conversation_runs")
        }
    engine.dispose()
