from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from e2e_server_error_checker import DatabaseLifecycleError, database_lifecycle_errors


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("asyncpg.exceptions.DeadlockDetectedError", DatabaseLifecycleError.DEADLOCK),
        ("psycopg.errors.DeadlockDetected", DatabaseLifecycleError.DEADLOCK),
        ("asyncpg.exceptions.ForeignKeyViolationError", DatabaseLifecycleError.FOREIGN_KEY),
        ("psycopg.errors.ForeignKeyViolation", DatabaseLifecycleError.FOREIGN_KEY),
    ],
)
def test_detects_background_database_failure_despite_successful_browser_run(
    marker: str, expected: DatabaseLifecycleError
) -> None:
    # Given: a logged database exception after the browser's successful action.
    stderr = f"[WebServer] [ERROR] finalization failed: {marker}\n"
    # When: Nightly scans the redacted server log.
    errors = database_lifecycle_errors(stderr)
    # Then: a swallowed task failure cannot become a green Nightly result.
    assert errors == frozenset({expected})


def test_deduplicates_both_error_classes() -> None:
    stderr = "DeadlockDetectedError\nDeadlockDetectedError\nForeignKeyViolationError"
    assert database_lifecycle_errors(stderr) == frozenset(DatabaseLifecycleError)


def test_allows_expected_scripted_model_errors_and_cancellation_warnings() -> None:
    stderr = (
        "RuntimeError('RateLimitError: E2E scripted model error simulation')\n"
        "psycopg.pool: discarding closed connection\n"
    )
    assert database_lifecycle_errors(stderr) == frozenset()


@pytest.mark.parametrize(
    ("content", "exit_code", "code"),
    [
        ("normal shutdown", 0, "e2e_server_errors=none"),
        ("DeadlockDetectedError secret-fixture", 1, "database_deadlock"),
        ("ForeignKeyViolationError secret-fixture", 1, "database_foreign_key_violation"),
    ],
)
def test_cli_exit_and_redaction(tmp_path: Path, content: str, exit_code: int, code: str) -> None:
    # Given: an already validated redacted export, including a path with spaces.
    log = tmp_path / "execution stderr.log"
    log.write_text(content, encoding="utf-8")
    cli = Path(__file__).resolve().parents[2] / "scripts/check-e2e-server-errors.py"
    # When: the actual CI command scans it.
    result = subprocess.run([sys.executable, str(cli), str(log)], capture_output=True, text=True)
    # Then: fail loudly with stable codes without reprinting source content.
    assert result.returncode == exit_code
    assert code in result.stdout + result.stderr
    assert "secret-fixture" not in result.stdout + result.stderr


def test_cli_fails_closed_when_log_missing(tmp_path: Path) -> None:
    cli = Path(__file__).resolve().parents[2] / "scripts/check-e2e-server-errors.py"
    result = subprocess.run(
        [sys.executable, str(cli), str(tmp_path / "absent.log")], capture_output=True, text=True
    )
    assert result.returncode == 2
    assert result.stderr.strip() == "e2e_server_log_unreadable"
