"""Failure-evidence contracts for the disposable PostgreSQL runner."""

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


def test_failed_lifecycle_retains_redacted_traceback_after_owned_root_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given an early traceback, sensitive dummy values, and a long final summary.
    child_output = "\n".join(
        [
            "_____ test_archive_first _____",
            "tests/integration/test_agent_lifecycle_chat_races.py:91: in test_archive_first",
            "Authorization: Bearer dummy-bearer-secret",
            "Cookie: moldy_at=dummy-cookie-secret",
            "tests/integration/test_agent_lifecycle_chat_races.py:103: AssertionError",
            'sqlalchemy.exc.ProgrammingError: column "signup_app" '
            'of relation "users" does not exist',
            "x" * 13_000,
        ]
    )
    _configure_failed_lifecycle_scenario(monkeypatch, tmp_path, child_output)

    # When the failed child outcome survives owned-root cleanup.
    outcome = postgres_test_runner._run_scenario(
        "agent-lifecycle", process_id=999999, process_identity="d" * 64
    )

    # Then actionable structural trace data remains without local secret values.
    assert outcome["failure_reason"] == "child_exit", outcome
    assert outcome["cleanup_run_root_removed"] is True
    assert not Path(str(outcome["run_root"])).exists()
    diagnostics = outcome["child_failure_diagnostics"]
    assert isinstance(diagnostics, dict)
    serialized = json.dumps(diagnostics, sort_keys=True)
    assert "test_agent_lifecycle_chat_races.py:91:in test_archive_first" in serialized
    assert "test_agent_lifecycle_chat_races.py:103:AssertionError" in serialized
    assert "dummy-bearer-secret" not in serialized
    assert "dummy-cookie-secret" not in serialized
    assert diagnostics["raw_output_chars"] == len(child_output)
    assert diagnostics["raw_output_retained"] is False
    assert diagnostics["structural_records_truncated"] is False
    assert diagnostics["schema_identifiers"] == ["column:signup_app", "relation:users"]


def test_failure_diagnostics_reports_record_and_text_truncation_without_values() -> None:
    # Given more structural records than the manifest bound and one oversized path.
    long_path = f"tests/{'a' * 600}.py:1: in test_long_path"
    locations = [f"tests/example_{index}.py:2: in test_failure" for index in range(69)]
    output = "\n".join([long_path, *locations, "Authorization: Bearer never-retain-this-value"])

    # When structural diagnostics are built from the full child output.
    diagnostics = build_child_failure_diagnostics(output)

    # Then both bounds and omission truth remain explicit without raw values.
    assert diagnostics["structural_record_count"] == STRUCTURAL_RECORD_LIMIT
    assert diagnostics["omitted_structural_record_count"] == 6
    assert diagnostics["structural_records_truncated"] is True
    assert diagnostics["record_text_truncated_count"] == 1
    assert all(len(record) <= STRUCTURAL_RECORD_CHAR_LIMIT for record in diagnostics["records"])
    assert "never-retain-this-value" not in json.dumps(diagnostics, sort_keys=True)


def test_failure_diagnostics_accepts_only_safe_pipe_indented_exception_group_lines() -> None:
    # Given pytest ExceptionGroup formatting around safe frames and unsafe arbitrary body lines.
    output = "\n".join(
        [
            "  | tests/integration/test_agent_lifecycle_chat_races.py:91: in test_archive_first",
            "  | tests/integration/test_agent_lifecycle_chat_races.py:103: AssertionError",
            "  | Authorization: Bearer pipe-secret-must-not-survive",
            "  | local_secret=another-pipe-secret",
            "  | ../../outside.py:3: in malformed_path",
            "  | tests/integration/test_agent_lifecycle_chat_races.py:not-a-line: ValueError",
        ]
    )

    # When the structural parser receives pipe-indented group output.
    diagnostics = build_child_failure_diagnostics(output)

    # Then only the same strict safe frame and terminal exception records survive.
    assert diagnostics["records"] == [
        "tests/integration/test_agent_lifecycle_chat_races.py:91:in test_archive_first",
        "tests/integration/test_agent_lifecycle_chat_races.py:103:AssertionError",
    ]
    serialized = json.dumps(diagnostics, sort_keys=True)
    assert "pipe-secret-must-not-survive" not in serialized
    assert "another-pipe-secret" not in serialized
    assert "outside.py" not in serialized
    assert "not-a-line" not in serialized


def test_timeout_salvages_valid_running_progress_before_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_failed_lifecycle_scenario(monkeypatch, tmp_path, "unused")

    def timeout_child(environment: dict[str, str], _kind: str) -> tuple[int, str]:
        Path(environment["MOLDY_PG_TEST_RECEIPT"]).write_text(
            json.dumps(_test_receipt(state="running")),
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(["pytest"], 600)

    monkeypatch.setattr(postgres_test_runner, "run_test_child", timeout_child)

    outcome = postgres_test_runner._run_scenario(
        "agent-lifecycle", process_id=999999, process_identity="d" * 64
    )

    assert outcome["status"] == "failed"
    assert outcome["failure_reason"] == "child_timeout"
    assert outcome["child_exit_code"] == 70
    assert outcome["test_receipt"] == _test_receipt(state="running")
    assert outcome["cleanup_run_root_removed"] is True


@pytest.mark.parametrize(
    "case",
    [
        _TimeoutReceiptCase(None, "child_timeout_progress_receipt_missing"),
        _TimeoutReceiptCase(
            b'{"schema_version":1,"secret":"must-not-survive"}',
            "child_timeout_invalid_progress_receipt",
        ),
    ],
)
def test_timeout_missing_or_malformed_progress_fails_closed_without_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: _TimeoutReceiptCase,
) -> None:
    _configure_failed_lifecycle_scenario(monkeypatch, tmp_path, "unused")

    def timeout_child(environment: dict[str, str], _kind: str) -> tuple[int, str]:
        if case.receipt_bytes is not None:
            Path(environment["MOLDY_PG_TEST_RECEIPT"]).write_bytes(case.receipt_bytes)
        raise subprocess.TimeoutExpired(["pytest"], 600)

    monkeypatch.setattr(postgres_test_runner, "run_test_child", timeout_child)

    outcome = postgres_test_runner._run_scenario(
        "agent-lifecycle", process_id=999999, process_identity="d" * 64
    )

    assert outcome["status"] == "failed"
    assert outcome["failure_reason"] == case.reason
    assert outcome["test_receipt"] is None
    assert outcome["cleanup_run_root_removed"] is True
    assert "must-not-survive" not in json.dumps(outcome, sort_keys=True)


def test_normal_child_rejects_unfinished_progress_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_failed_lifecycle_scenario(monkeypatch, tmp_path, "unused")

    def unfinished_child(environment: dict[str, str], _kind: str) -> tuple[int, str]:
        Path(environment["MOLDY_PG_TEST_RECEIPT"]).write_text(
            json.dumps(_test_receipt(state="running")), encoding="utf-8"
        )
        return 0, ""

    monkeypatch.setattr(postgres_test_runner, "run_test_child", unfinished_child)

    outcome = postgres_test_runner._run_scenario(
        "agent-lifecycle", process_id=999999, process_identity="d" * 64
    )

    assert outcome["status"] == "failed"
    assert outcome["failure_reason"] == "ProgressReceiptError"
    assert outcome["test_receipt"] is None
    assert outcome["cleanup_run_root_removed"] is True


def test_normal_child_rejects_untrusted_resource_keys_and_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_failed_lifecycle_scenario(monkeypatch, tmp_path, "unused")

    def untrusted_child(environment: dict[str, str], _kind: str) -> tuple[int, str]:
        payload = _test_receipt()
        payload["resource_receipt"] = {
            "database_checked_out": 0,
            "checkpointer_pool_published": False,
            "checkpointer_published": False,
            "conversation_tasks_active": 0,
            "skill_worker_task_active": False,
            "secret-resource-key": "must-not-survive",
        }
        Path(environment["MOLDY_PG_TEST_RECEIPT"]).write_text(json.dumps(payload), encoding="utf-8")
        return 0, ""

    monkeypatch.setattr(postgres_test_runner, "run_test_child", untrusted_child)

    outcome = postgres_test_runner._run_scenario(
        "agent-lifecycle", process_id=999999, process_identity="d" * 64
    )

    assert outcome["status"] == "failed"
    assert outcome["failure_reason"] == "ProgressReceiptError"
    assert outcome["test_receipt"] is None
    assert outcome["cleanup_run_root_removed"] is True
    assert "secret-resource-key" not in json.dumps(outcome, sort_keys=True)
    assert "must-not-survive" not in json.dumps(outcome, sort_keys=True)
