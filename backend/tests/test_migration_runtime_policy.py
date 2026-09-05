"""M71 runtime-policy persistence and isolated PostgreSQL lane contracts."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import inspect

BACKEND_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_ROOT = BACKEND_ROOT / "alembic" / "versions"
MIGRATION_FILENAME = "m71_runtime_policy.py"
sys.path.insert(0, str(BACKEND_ROOT.parent / "scripts"))

import postgres_manifest_io  # noqa: E402
import postgres_test_runner  # noqa: E402
from postgres_cleanup_checker import validate_payload  # noqa: E402
from postgres_runner_contract import build_lane_dsns  # noqa: E402
from postgres_runner_runtime import (  # noqa: E402
    ScenarioKind,
    alembic_migration_roundtrip,
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"{MIGRATION_FILENAME.removesuffix('.py')}_test_load",
        VERSIONS_ROOT / MIGRATION_FILENAME,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m71_revision_chain_is_linear_after_m70() -> None:
    # Given the checked-in migration module.
    migration = _load_migration()

    # When its literal Alembic metadata is inspected.

    # Then M71 has exactly the current M70 head as its parent.
    assert migration.revision == "m71_runtime_policy"
    assert migration.down_revision == "m70_skill_usage_and_feedback"
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)


@pytest.mark.asyncio
async def test_m71_nullable_json_column_roundtrips_without_backfill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a legacy agents table with an existing row.
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    from alembic import op as alembic_op

    migration = _load_migration()
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("CREATE TABLE agents (id TEXT PRIMARY KEY)")
            connection.exec_driver_sql("INSERT INTO agents (id) VALUES ('legacy')")
            context = MigrationContext.configure(connection)
            monkeypatch.setattr(alembic_op, "_proxy", Operations(context), raising=False)

            # When M71 upgrades the legacy schema.
            migration.upgrade()

            # Then the additive JSON column exists, legacy data stays NULL, and old writes work.
            columns = {
                column["name"]: column for column in inspect(connection).get_columns("agents")
            }
            assert columns["runtime_policy"]["nullable"] is True
            assert (
                connection.exec_driver_sql(
                    "SELECT runtime_policy FROM agents WHERE id='legacy'"
                ).scalar_one()
                is None
            )
            connection.exec_driver_sql("INSERT INTO agents (id) VALUES ('old-client')")
            assert (
                connection.exec_driver_sql(
                    "SELECT runtime_policy FROM agents WHERE id='old-client'"
                ).scalar_one()
                is None
            )

            # When the migration is downgraded in the disposable test database.
            migration.downgrade()

            # Then only the additive column is removed.
            assert "runtime_policy" not in {
                column["name"] for column in inspect(connection).get_columns("agents")
            }
    finally:
        engine.dispose()


def test_postgres_roundtrip_downgrades_one_revision_then_restores_exact_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a migrated isolated lane and a deterministic schema fingerprint.
    dsns = build_lane_dsns(
        user="runner",
        password="dummy",
        port=49152,
        database="moldy_pg_lane_a1",
    )
    environment = {
        "DATABASE_URL": dsns.async_url,
        "DATABASE_URL_SYNC": dsns.sync_url,
        "INTEGRATION_DATABASE_URL": dsns.integration_url,
    }
    commands: list[list[str]] = []

    def record_command(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        if argv[-1] == "current":
            current = "m70_skill_usage_and_feedback" if len(commands) == 2 else "m71_runtime_policy"
            return subprocess.CompletedProcess(argv, 0, f"{current} (head)\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("postgres_runner_runtime.run_command", record_command)
    monkeypatch.setattr("postgres_runner_runtime._fingerprint", lambda _dsn: "after")

    # When the dedicated migration scenario performs a one-step rollback and restore.
    result = alembic_migration_roundtrip(environment, "m71_runtime_policy", "after")

    # Then it uses only the disposable lane and proves the restored schema is exact.
    assert result is True
    assert [[Path(command[0]).name, *command[1:]] for command in commands] == [
        ["alembic", "downgrade", "-1"],
        ["alembic", "current"],
        ["alembic", "upgrade", "head"],
        ["alembic", "current"],
    ]


def test_postgres_manifest_accepts_completed_migration_roundtrip() -> None:
    # Given a completed isolated M71 downgrade/upgrade receipt.
    scenario = {
        "scenario": "migration-roundtrip",
        "status": "passed",
        "failure_reason": None,
        "child_exit_code": 0,
        "image": "postgres:16-alpine",
        "server_version_num": "160010",
        "container_id": "a" * 64,
        "run_id": "b" * 24,
        "container_id_sha256": ("ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb"),
        "port": 49152,
        "run_root": "/tmp/.moldy-pg-run-example",
        "run_root_created": True,
        "tmpfs_storage": True,
        "storage_inspected": True,
        "port_mapping_observed": True,
        "run_root_device": 1,
        "run_root_inode": 2,
        "process_id": 999999,
        "process_identity_sha256": "d" * 64,
        "alembic_head": "m71_runtime_policy",
        "alembic_current": "m71_runtime_policy",
        "schema_fingerprint": "c" * 64,
        "second_upgrade_idempotent": True,
        "migration_roundtrip": True,
        "warning_hits": [],
        "warning_scan_complete": True,
        "test_receipt": None,
        "cleanup_container_removed": True,
        "owned_label_absent": True,
        "port_mapping_removed": True,
        "process_group_stopped": True,
        "cleanup_run_root_removed": True,
        "foreign_containers_observed": True,
        "foreign_containers_preserved": True,
    }
    payload = {
        "schema_version": 1,
        "mode": "migration-roundtrip",
        "status": "passed",
        "concurrent_pair": False,
        "scenarios": [scenario],
    }

    # When the public cleanup checker validates the receipt.
    validate_payload(payload)

    # Then no exception is raised.


def test_postgres_cli_dispatches_migration_roundtrip_as_one_owned_scenario(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given a manifest adapter and the new public runner mode.
    captured: list[dict[str, object]] = []
    calls: list[ScenarioKind] = []
    dummy = postgres_manifest_io.EvidenceDirectory(tmp_path, -1, 1, 1)
    monkeypatch.setattr(postgres_manifest_io, "ensure_evidence_root", lambda _repo: tmp_path)
    monkeypatch.setattr(
        postgres_manifest_io,
        "validate_manifest_destination",
        lambda path, _root: path,
    )
    monkeypatch.setattr(postgres_manifest_io, "open_evidence_directory", lambda _root: dummy)
    monkeypatch.setattr(postgres_manifest_io, "close_evidence_directory", lambda _root: None)
    monkeypatch.setattr(postgres_manifest_io, "verify_evidence_directory", lambda _root: None)
    monkeypatch.setattr(postgres_manifest_io, "process_identity_sha256", lambda: "d" * 64)
    monkeypatch.setattr(
        postgres_manifest_io,
        "write_manifest",
        lambda _path, payload, _root: captured.append(payload),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["postgres_test_runner.py", "migration-roundtrip", "--manifest", str(tmp_path / "x")],
    )

    def run_scenario(
        kind: ScenarioKind,
        *,
        process_id: int,
        process_identity: str,
    ) -> dict[str, object]:
        del process_id, process_identity
        calls.append(kind)
        return {"scenario": kind, "status": "passed"}

    # When the canonical CLI dispatches the mode.
    exit_code = postgres_manifest_io.run_cli(
        run_scenario,
        postgres_test_runner._early_interrupt_outcome,
    )

    # Then it owns one serialized scenario and preserves the mode in evidence.
    assert exit_code == 1  # incomplete fake cleanup is correctly not reported as success
    assert calls == ["migration-roundtrip"]
    assert captured[0]["mode"] == "migration-roundtrip"
