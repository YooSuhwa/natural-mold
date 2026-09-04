"""Canonical disposable PostgreSQL integration-test lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
from pathlib import Path

import psycopg
from postgres_manifest_io import (
    RunnerInterrupted,
    cleanup_receipt_succeeded,
    defer_cleanup_signals,
    run_cli,
)
from postgres_runner_cleanup import CleanupContext, cleanup_scenario_resources
from postgres_runner_contract import (
    build_lane_dsns,
    parse_lane_dsns,
)
from postgres_runner_runtime import (
    ExternalScenarioKind,
    OwnedContainer,
    ScenarioKind,
    alembic_migration_roundtrip,
    alembic_state,
    build_docker_env,
    build_docker_run_argv,
    docker_ids,
    inspect_identity,
    inspect_storage,
    lane_env,
    mapped_port,
    run_command,
    run_test_child,
    start_process_scope,
    wait_ready,
)
from psycopg import sql


def _initial_outcome(
    kind: str,
    run_id: str,
    run_root: Path | None,
    *,
    process_id: int,
    process_identity: str,
) -> dict[str, object]:
    outcome: dict[str, object] = {
        "scenario": kind,
        "status": "failed",
        "failure_reason": "not_started",
        "child_exit_code": 70,
        "image": "postgres:16-alpine",
        "run_id": run_id,
        "run_root_created": run_root is not None,
        "process_id": process_id,
        "process_identity_sha256": process_identity,
        "tmpfs_storage": False,
        "storage_inspected": False,
        "port_mapping_observed": False,
        "test_receipt": None,
        "warning_hits": [],
        "warning_scan_complete": False,
    }
    if run_root is not None:
        outcome["run_root"] = str(run_root)
    return outcome


def _run_scenario(
    kind: ScenarioKind, *, process_id: int, process_identity: str
) -> dict[str, object]:
    start_process_scope()
    owner_token = secrets.token_hex(32)
    run_id = secrets.token_hex(12)
    owner = OwnedContainer(owner_token, run_id, f"moldy-pg-{run_id}")
    run_root: Path | None = None
    root_stat: os.stat_result | None = None
    receipt: Path | None = None
    before: set[str] | None = None
    outcome = _initial_outcome(
        kind, run_id, None, process_id=process_id, process_identity=process_identity
    )
    try:
        run_root = Path(tempfile.mkdtemp(prefix=".moldy-test-run.pg-"))
        run_root.chmod(stat.S_IRWXU)
        root_stat = run_root.stat()
        receipt = run_root / "pytest-receipt.json"
        outcome.update(
            {
                "run_root": str(run_root),
                "run_root_created": True,
                "run_root_device": root_stat.st_dev,
                "run_root_inode": root_stat.st_ino,
            }
        )
        before = docker_ids()
        password = secrets.token_urlsafe(24)
        docker_env = build_docker_env(password)
        created = run_command(
            build_docker_run_argv(owner),
            env=docker_env,
            timeout=60,
        )
        if created.returncode != 0:
            raise RuntimeError("container_create_failed")
        container_id = created.stdout.strip()
        outcome.update(
            {
                "container_id": container_id,
                "container_id_sha256": hashlib.sha256(container_id.encode()).hexdigest(),
            }
        )
        owner.container_id = container_id
        if inspect_identity(owner.container_id) != (owner.container_id, owner_token):
            raise RuntimeError("container_identity_mismatch")
        tmpfs_owned, port_mapped = inspect_storage(owner.container_id, owner_token)
        outcome.update({"tmpfs_storage": tmpfs_owned, "storage_inspected": True})
        if not tmpfs_owned or not port_mapped:
            raise RuntimeError("container_storage_or_port_mismatch")
        owner.port = mapped_port(owner.container_id)
        outcome.update({"port": owner.port, "port_mapping_observed": True})
        database = f"moldy_pg_lane_{run_id}"
        admin = build_lane_dsns(
            user="runner",
            password=password,
            port=owner.port,
            database="moldy_pg_lane_admin",
        )
        admin_url = admin.sync_url.replace("moldy_pg_lane_admin", "postgres")
        server = wait_ready(owner.container_id, admin_url)
        dsns = build_lane_dsns(user="runner", password=password, port=owner.port, database=database)
        parse_lane_dsns(dsns)
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        env = lane_env(dsns, run_root, receipt)
        head, current, fingerprint, idempotent = alembic_state(env)
        if kind == "migration-roundtrip":
            migration_roundtrip = alembic_migration_roundtrip(env, head, fingerprint)
            exit_code, output = (0 if migration_roundtrip else 1), ""
        else:
            migration_roundtrip = None
            exit_code, output = run_test_child(env, kind)
        warnings = sorted(
            set(
                re.findall(
                    r"(?:SAWarning|ResourceWarning|garbage collector|non-checked-in connection)",
                    output,
                    re.IGNORECASE,
                )
            )
        )
        expected = {
            "all": 0,
            "migration-roundtrip": 0,
            "stream-resume": 0,
            "success": 0,
            "child_failure": 23,
            "sigint": 130,
        }[kind]
        outcome.update(
            {
                "status": "passed" if exit_code == expected else "failed",
                "failure_reason": None if exit_code == expected else "child_exit",
                "child_exit_code": exit_code,
                "server_version_num": server,
                "alembic_head": head,
                "alembic_current": current,
                "schema_fingerprint": fingerprint,
                "second_upgrade_idempotent": idempotent,
                "warning_hits": warnings,
                "warning_scan_complete": True,
                "test_receipt": json.loads(receipt.read_text())
                if kind in {"all", "stream-resume"}
                else None,
                "tmpfs_storage": tmpfs_owned,
            }
        )
        if migration_roundtrip is not None:
            outcome["migration_roundtrip"] = migration_roundtrip
    except RunnerInterrupted as interrupted:
        outcome.update(
            {
                "status": "interrupted",
                "failure_reason": "signal",
                "child_exit_code": 128 + interrupted.signal_number,
            }
        )
    except Exception as error:  # noqa: BLE001 - scenario boundary must emit cleanup receipt
        outcome["failure_reason"] = type(error).__name__
    finally:
        with defer_cleanup_signals() as deferred_signals:
            captured_id = outcome.get("container_id")
            if owner.container_id is None and isinstance(captured_id, str):
                owner.container_id = captured_id
            outcome.update(
                cleanup_scenario_resources(
                    CleanupContext(
                        owner=owner,
                        owner_token=owner_token,
                        run_root=run_root,
                        root_device=root_stat.st_dev if root_stat is not None else None,
                        root_inode=root_stat.st_ino if root_stat is not None else None,
                        before_ids=before,
                    )
                )
            )
        if not cleanup_receipt_succeeded(outcome):
            outcome.update({"status": "failed", "failure_reason": "cleanup_failed"})
        elif deferred_signals:
            strongest = max(deferred_signals)
            outcome.update(
                {
                    "status": "interrupted",
                    "failure_reason": "signal",
                    "child_exit_code": 128 + strongest,
                }
            )
    return outcome


def _early_interrupt_outcome(
    signal_number: int,
    *,
    process_id: int,
    process_identity: str,
    mode: ExternalScenarioKind = "all",
) -> dict[str, object]:
    """Emit only cleanup facts knowable before resource acquisition."""
    outcome = _initial_outcome(
        mode,
        secrets.token_hex(12),
        None,
        process_id=process_id,
        process_identity=process_identity,
    )
    outcome.update(
        {
            "status": "interrupted",
            "failure_reason": "signal",
            "child_exit_code": 128 + signal_number,
            "cleanup_container_removed": True,
            "owned_label_absent": True,
            "port_mapping_removed": True,
            "process_group_stopped": True,
            "cleanup_run_root_removed": True,
            "foreign_containers_observed": False,
            "foreign_containers_preserved": None,
        }
    )
    return outcome


def main() -> int:
    return run_cli(_run_scenario, _early_interrupt_outcome)


if __name__ == "__main__":
    raise SystemExit(main())
