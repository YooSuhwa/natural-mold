"""Child-failure evidence contracts for the disposable PostgreSQL runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.postgres_failure_diagnostics_support import (
    STRUCTURAL_RECORD_CHAR_LIMIT,
    STRUCTURAL_RECORD_LIMIT,
    _configure_failed_lifecycle_scenario,
    build_child_failure_diagnostics,
    postgres_test_runner,
)


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
