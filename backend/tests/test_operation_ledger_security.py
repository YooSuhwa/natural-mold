"""Security boundary tests for bounded operation arguments."""

from __future__ import annotations

import importlib.util
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
    spec = importlib.util.spec_from_file_location("operation_security_test", WRITER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with _writer_import_path():
        spec.loader.exec_module(module)
    return module


def _ledger(writer: ModuleType, tmp_path: Path) -> Path:
    facts: dict[str, JSONValue] = {
        "base_sha": "7" * 40,
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
    ledger = tmp_path / "operations.ndjson"
    writer.write_genesis(
        ledger,
        writer.build_genesis(
            facts,
            clock=lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        ),
    )
    return ledger


@pytest.mark.parametrize(
    "payload",
    [
        {"details": [{"CrEdEnTiAl": "DUMMY_VALUE"}]},
        {"note": "Bearer DUMMY_VALUE"},
        {"note": "Basic ZHVtbXk="},
        {"note": "credential=DUMMY_VALUE"},
    ],
)
def test_append_rejects_nested_credential_keys_and_secret_values(
    tmp_path: Path, payload: dict[str, JSONValue]
) -> None:
    # Given: a valid ledger and nested or value-shaped dummy credential material.
    writer = _load_writer()
    ledger = _ledger(writer, tmp_path)

    # When / Then: recursive classification fails closed.
    with pytest.raises(writer.LedgerError):
        writer.append_operation(
            ledger,
            task_id="02",
            action_class="security_test",
            arguments=payload,
            status="failed",
        )


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "/private/tmp/ledger",
        "~/ledger",
        "C:\\temp\\ledger",
        "\\\\server\\share\\ledger",
        "//server/share/ledger",
        "safe/../outside",
        "safe\\..\\outside",
        "completed with api_key=DUMMY_VALUE",
        "completed with token=DUMMY_VALUE",
        "completed with password=DUMMY_VALUE",
        "completed with credential=DUMMY_VALUE",
    ],
)
def test_append_rejects_raw_paths_and_embedded_assignments_under_ordinary_keys(
    tmp_path: Path, unsafe_value: str
) -> None:
    # Given: an unsafe string is nested beneath an ordinary key and list.
    writer = _load_writer()
    ledger = _ledger(writer, tmp_path)
    original = ledger.read_bytes()

    # When / Then: public append rejects it without changing the verified prefix.
    with pytest.raises(writer.LedgerError):
        writer.append_operation(
            ledger,
            task_id="02",
            action_class="security_test",
            arguments={"details": [{"note": unsafe_value}]},
            status="failed",
        )
    assert ledger.read_bytes() == original


def test_append_preserves_safe_prose_and_exact_size_boundary(tmp_path: Path) -> None:
    # Given: prose that mentions auth concepts without carrying a secret.
    writer = _load_writer()
    ledger = _ledger(writer, tmp_path)
    overhead = len(writer.canonical_line({"note": ""}))
    exact = {"note": "x" * (16_384 - overhead)}
    oversized = {"note": "x" * (16_385 - overhead)}

    # When: safe prose and an exactly bounded payload are appended.
    writer.append_operation(
        ledger,
        task_id="02",
        action_class="security_test",
        arguments={
            "note": "Current controls reject credential assignments and physical paths.",
            "identifier": "task-02/result",
            "url": "https://example.test/api/v1",
        },
        status="passed",
    )
    writer.append_operation(
        ledger,
        task_id="02",
        action_class="security_test",
        arguments=exact,
        status="passed",
    )

    # Then: only the payload beyond the canonical byte limit is rejected.
    assert len(writer.canonical_line(exact)) == 16_384
    with pytest.raises(writer.LedgerError):
        writer.append_operation(
            ledger,
            task_id="02",
            action_class="security_test",
            arguments=oversized,
            status="failed",
        )
