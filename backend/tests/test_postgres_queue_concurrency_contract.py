from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT.parent / "scripts"))

from postgres_runner_contract import build_lane_dsns  # noqa: E402
from postgres_runner_runtime import run_test_child  # noqa: E402


def test_queue_concurrency_runner_selects_reviewed_queue_and_steering_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a disposable-lane environment and command capture.
    dsns = build_lane_dsns(
        user="runner",
        password="dummy",
        port=49152,
        database="moldy_pg_lane_queue",
    )
    environment = {
        "DATABASE_URL": dsns.async_url,
        "DATABASE_URL_SYNC": dsns.sync_url,
        "INTEGRATION_DATABASE_URL": dsns.integration_url,
    }
    commands: list[list[str]] = []

    def record_command(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("postgres_runner_runtime.run_command", record_command)

    # When the named queue lane resolves its child command.
    exit_code, _output = run_test_child(environment, "queue-concurrency")

    # Then queue locks, steering races and cross-process recovery run together.
    assert exit_code == 0
    assert commands == [
        [
            str(BACKEND_ROOT / ".venv/bin/pytest"),
            "-q",
            "-p",
            "tests.postgres_execution_plugin",
            "tests/integration/test_conversation_queue_concurrency.py",
            "tests/integration/test_chat_steer_concurrency.py",
            "tests/integration/test_ag_ui_v1_read_faults_postgres.py",
            "tests/integration/test_steer_recovery.py",
            (
                "tests/test_chat_steer_checkpoint.py"
                "::test_new_process_recovers_consumed_receipt_and_leaves_prepared_row_unconfirmed"
            ),
            "-m",
            "integration",
        ]
    ]
