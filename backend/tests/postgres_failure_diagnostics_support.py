"""Shared fixtures for PostgreSQL runner failure-evidence tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT.parent / "scripts"))

import postgres_test_runner  # noqa: E402
from postgres_failure_diagnostics import (  # noqa: E402
    STRUCTURAL_RECORD_CHAR_LIMIT,
    STRUCTURAL_RECORD_LIMIT,
    build_child_failure_diagnostics,
)
from postgres_runner_cleanup import CleanupContext  # noqa: E402

__all__ = [
    "STRUCTURAL_RECORD_CHAR_LIMIT",
    "STRUCTURAL_RECORD_LIMIT",
    "build_child_failure_diagnostics",
    "postgres_test_runner",
]


@dataclass(frozen=True, slots=True)
class _TimeoutReceiptCase:
    receipt_bytes: bytes | None
    reason: str


def _test_receipt(*, state: str = "finished") -> dict[str, object]:
    selected = ["tests/example.py::test_failure"]
    completed = selected if state == "finished" else []
    resource_receipt = (
        {
            "database_checked_out": 0,
            "checkpointer_pool_published": False,
            "checkpointer_published": False,
            "conversation_tasks_active": 0,
            "skill_worker_task_active": False,
        }
        if state == "finished"
        else None
    )
    return {
        "schema_version": 1,
        "selected_node_ids": selected,
        "executed_node_ids": selected if state == "finished" else [],
        "deselected_node_ids": [],
        "failed_node_ids": selected if state == "finished" else [],
        "skipped_node_ids": [],
        "selected_sha256": hashlib.sha256("\n".join(selected).encode()).hexdigest(),
        "resource_receipt": resource_receipt,
        "progress": {
            "schema_version": 1,
            "sequence": 4,
            "state": state,
            "session_elapsed_ns": 100,
            "active_node_id": None if state == "finished" else selected[0],
            "active_phase": "session_finish" if state == "finished" else "call",
            "completed_node_ids": completed,
            "node_elapsed_ns": {selected[0]: 80} if completed else {},
        },
    }


def _configure_failed_lifecycle_scenario(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, child_output: str
) -> None:
    run_root = tmp_path / "owned-run-root"

    def isolated_mkdtemp(*, prefix: str) -> str:
        assert prefix == ".moldy-test-run.pg-"
        run_root.mkdir()
        return str(run_root)

    def create_container(
        argv: list[str], **_kwargs: str | int | dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "a" * 64, "")

    def failed_child(environment: dict[str, str], _kind: str) -> tuple[int, str]:
        receipt = Path(environment["MOLDY_PG_TEST_RECEIPT"])
        receipt.write_text(json.dumps(_test_receipt()), encoding="utf-8")
        return 1, child_output

    def remove_owned_root(context: CleanupContext) -> dict[str, bool]:
        assert context.run_root == run_root
        shutil.rmtree(run_root)
        return {
            "cleanup_container_removed": True,
            "owned_label_absent": True,
            "port_mapping_removed": True,
            "process_group_stopped": True,
            "cleanup_run_root_removed": True,
            "foreign_containers_observed": True,
            "foreign_containers_preserved": True,
        }

    monkeypatch.setattr(postgres_test_runner.tempfile, "mkdtemp", isolated_mkdtemp)
    monkeypatch.setattr(postgres_test_runner, "start_process_scope", lambda: None)
    monkeypatch.setattr(postgres_test_runner, "docker_ids", lambda: {"foreign"})
    monkeypatch.setattr(postgres_test_runner, "build_docker_env", lambda _password: {})
    monkeypatch.setattr(postgres_test_runner, "build_docker_run_argv", lambda _owner: ["docker"])
    monkeypatch.setattr(postgres_test_runner, "run_command", create_container)
    monkeypatch.setattr(
        postgres_test_runner.secrets,
        "token_hex",
        lambda size: "c" * 64 if size == 32 else "b" * 24,
    )
    monkeypatch.setattr(
        postgres_test_runner.secrets, "token_urlsafe", lambda _size: "database-password"
    )
    monkeypatch.setattr(
        postgres_test_runner,
        "inspect_identity",
        lambda identifier: (identifier, "c" * 64),
    )
    monkeypatch.setattr(postgres_test_runner, "inspect_storage", lambda _id, _token: (True, True))
    monkeypatch.setattr(postgres_test_runner, "mapped_port", lambda _id: 49152)
    monkeypatch.setattr(postgres_test_runner, "wait_ready", lambda _id, _url: "160015")
    monkeypatch.setattr(
        postgres_test_runner.psycopg,
        "connect",
        lambda _url, *, autocommit: nullcontext(MagicMock()),
    )
    monkeypatch.setattr(
        postgres_test_runner,
        "alembic_state",
        lambda _env: ("b08_agent_lifecycle", "b08_agent_lifecycle", "f" * 64, True),
    )
    monkeypatch.setattr(postgres_test_runner, "run_test_child", failed_child)
    monkeypatch.setattr(postgres_test_runner, "cleanup_scenario_resources", remove_owned_root)
    monkeypatch.setattr(postgres_test_runner, "cleanup_receipt_succeeded", lambda _outcome: True)
