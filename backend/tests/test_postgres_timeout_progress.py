"""Timeout and progress-receipt contracts for the disposable PostgreSQL runner."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.postgres_failure_diagnostics_support import (
    _configure_failed_lifecycle_scenario,
    _test_receipt,
    _TimeoutReceiptCase,
    postgres_test_runner,
)


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
