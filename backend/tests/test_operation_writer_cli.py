"""Real subprocess characterization for the operations-writer CLI."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRITER_PATH = REPO_ROOT / "scripts" / "append-operation.py"

type JSONValue = None | bool | int | str | list[JSONValue] | dict[str, JSONValue]


@contextmanager
def _writer_import_path() -> Iterator[None]:
    original_path = sys.path[:]
    sys.path.insert(0, str(WRITER_PATH.parent))
    try:
        yield
    finally:
        sys.path[:] = original_path


def _load_writer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("append_operation_cli_test", WRITER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with _writer_import_path():
        spec.loader.exec_module(module)
    return module


def _fixed_clock() -> datetime:
    return datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _golden_facts() -> dict[str, JSONValue]:
    return {
        "base_sha": "7a9cee88c772e29c830cd84fb5578d06d753e080",
        "tracked_status": "clean",
        "expected_plan_sha": "1" * 64,
        "expected_review_round": "review-fixture",
        "source_plan_sha256": "1" * 64,
        "resolved_links": [
            {
                "logical_path": "backend/.env",
                "link_kind": "symlink",
                "target_kind": "file",
                "target_path_sha256": "2" * 64,
            },
            {
                "logical_path": "backend/data",
                "link_kind": "symlink",
                "target_kind": "directory",
                "target_path_sha256": "3" * 64,
            },
        ],
    }


def _ledger(tmp_path: Path) -> tuple[ModuleType, Path]:
    writer = _load_writer()
    ledger = tmp_path / "operations.ndjson"
    writer.write_genesis(ledger, writer.build_genesis(_golden_facts(), clock=_fixed_clock))
    return writer, ledger


def test_cli_append_runs_from_unrelated_working_directory(tmp_path: Path) -> None:
    # Given: a valid ledger and a cwd unrelated to the repository.
    writer, ledger = _ledger(tmp_path)

    # When: the absolute CLI path appends one bounded operation.
    result = subprocess.run(
        [
            sys.executable,
            str(WRITER_PATH),
            "append",
            "--operations",
            str(ledger),
            "--task-id",
            "02",
            "--action-class",
            "cli_characterization",
            "--status",
            "passed",
            "--arguments-json",
            '{"scenario":"unrelated cwd"}',
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: output is exact and the façade verifies a two-entry chain.
    assert result.returncode == 0
    assert result.stderr == ""
    assert re.fullmatch(r"sequence=1 hash=[0-9a-f]{64}\n", result.stdout)
    assert len(writer.verify_ledger(ledger)) == 2


def test_cli_error_redacts_argument_values(tmp_path: Path) -> None:
    # Given: a valid ledger and a clearly dummy unsafe value.
    _, ledger = _ledger(tmp_path)
    dummy_value = "DUMMY_UNSAFE_CLI_VALUE"

    # When: the CLI rejects a secret-like argument value.
    result = subprocess.run(
        [
            sys.executable,
            str(WRITER_PATH),
            "append",
            "--operations",
            str(ledger),
            "--task-id",
            "02",
            "--action-class",
            "cli_characterization",
            "--status",
            "failed",
            "--arguments-json",
            f'{{"note":"Bearer {dummy_value}"}}',
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: only the exception class is emitted and the value never appears.
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "operation ledger rejected: LedgerError\n"
    assert dummy_value not in result.stdout
    assert dummy_value not in result.stderr


@pytest.mark.parametrize(
    "payload",
    [
        {"note": "request Authorization: Bearer DUMMY_EMBEDDED_VALUE"},
        {"material": "-----BEGIN PRIVATE KEY-----\nDUMMY\n-----END PRIVATE KEY-----"},
        {"artifact_path": "safe/../outside"},
        {"details": [{"note": "/private/tmp/ledger"}]},
        {"details": [{"note": "~/ledger"}]},
        {"details": [{"note": "C:\\temp\\ledger"}]},
        {"details": [{"note": "\\\\server\\share\\ledger"}]},
        {"details": [{"note": "//server/share/ledger"}]},
        {"details": [{"note": "safe/../outside"}]},
        {"details": [{"note": "safe\\..\\outside"}]},
        {"details": [{"note": "completed with api_key=DUMMY_EMBEDDED_VALUE"}]},
        {"details": [{"note": "completed with token=DUMMY_EMBEDDED_VALUE"}]},
        {"details": [{"note": "completed with password=DUMMY_EMBEDDED_VALUE"}]},
        {"details": [{"note": "completed with credential=DUMMY_EMBEDDED_VALUE"}]},
    ],
)
def test_cli_rejects_embedded_credentials_private_keys_and_traversal(
    tmp_path: Path, payload: dict[str, JSONValue]
) -> None:
    # Given: a valid ledger and an unsafe value crossing the public CLI boundary.
    _, ledger = _ledger(tmp_path)
    original = ledger.read_bytes()

    # When: the CLI classifies the canonical JSON payload.
    result = subprocess.run(
        [
            sys.executable,
            str(WRITER_PATH),
            "append",
            "--operations",
            str(ledger),
            "--task-id",
            "02",
            "--action-class",
            "cli_security",
            "--status",
            "failed",
            "--arguments-json",
            json.dumps(payload),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: output is redacted and the verified prefix is byte-identical.
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "operation ledger rejected: LedgerError\n"
    assert ledger.read_bytes() == original


def test_cli_accepts_safe_security_prose(tmp_path: Path) -> None:
    # Given: prose about the security contract without credential material.
    writer, ledger = _ledger(tmp_path)

    # When: the public CLI appends the safe prose.
    result = subprocess.run(
        [
            sys.executable,
            str(WRITER_PATH),
            "append",
            "--operations",
            str(ledger),
            "--task-id",
            "02",
            "--action-class",
            "cli_security",
            "--status",
            "passed",
            "--arguments-json",
            '{"note":"Current policy rejects secret assignments and physical paths.",'
            '"identifier":"task-02/result","url":"https://example.test/api/v1"}',
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: the safe control appends and verifies normally.
    assert result.returncode == 0
    assert len(writer.verify_ledger(ledger)) == 2
