"""Contract tests for the append-only operations evidence ledger."""

from __future__ import annotations

import hashlib
import importlib.util
import multiprocessing
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

type JSONValue = None | bool | int | str | list[JSONValue] | dict[str, JSONValue]

REPO_ROOT = Path(__file__).resolve().parents[2]
WRITER_PATH = REPO_ROOT / "scripts" / "append-operation.py"
GOLDEN_HASH = "3456f105ed85bf2d8daba307c7ca8ffcb453ae09240fbda259ca33d1f171a8f1"
GOLDEN_LINE = (
    '{"action_class":"bootstrap","arguments":{"base_sha":"7a9cee88c772e29c830cd84fb5578d06d753e080",'
    '"expected_plan_sha":"1111111111111111111111111111111111111111111111111111111111111111",'
    '"expected_review_round":"review-fixture","resolved_links":[{"link_kind":"symlink",'
    '"logical_path":"backend/.env","target_kind":"file","target_path_sha256":'
    '"2222222222222222222222222222222222222222222222222222222222222222"},{"link_kind":'
    '"symlink","logical_path":"backend/data","target_kind":"directory","target_path_sha256":'
    '"3333333333333333333333333333333333333333333333333333333333333333"}],'
    '"source_plan_sha256":"1111111111111111111111111111111111111111111111111111111111111111",'
    '"tracked_status":"clean"},"entry_hash":"3456f105ed85bf2d8daba307c7ca8ffcb453ae09240fbda259ca33d1f171a8f1",'
    '"previous_entry_hash":null,"schema_version":1,"sequence":0,"status":"passed",'
    '"task_id":"02","timestamp_utc":"2026-01-02T03:04:05Z"}\n'
)


@contextmanager
def _writer_import_path() -> Iterator[None]:
    original_path = sys.path[:]
    sys.path.insert(0, str(WRITER_PATH.parent))
    try:
        yield
    finally:
        sys.path[:] = original_path


def _load_writer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("append_operation", WRITER_PATH)
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


def _append_worker(writer_path: str, ledger_path: str, worker: int) -> None:
    spec = importlib.util.spec_from_file_location(f"append_operation_{worker}", writer_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with _writer_import_path():
        spec.loader.exec_module(module)
    module.append_operation(
        Path(ledger_path),
        task_id="02",
        action_class="test",
        arguments={"worker": worker},
        status="passed",
    )


def test_golden_genesis_is_byte_exact() -> None:
    # Given: the immutable plan's fixed bootstrap vector.
    writer = _load_writer()

    # When: genesis is canonicalized using an injected clock.
    entry = writer.build_genesis(_golden_facts(), clock=_fixed_clock)
    line = writer.canonical_line(entry)

    # Then: both the pre-hash digest and final line match the literals.
    prehash = {key: value for key, value in entry.items() if key != "entry_hash"}
    assert hashlib.sha256(writer.canonical_line(prehash)).hexdigest() == GOLDEN_HASH
    assert line.decode() == GOLDEN_LINE


@pytest.mark.parametrize("mutation", ["missing", "extra", "reordered"])
def test_bootstrap_rejects_non_exact_facts(mutation: str) -> None:
    # Given: a missing, extra, or reordered bootstrap fact set.
    writer = _load_writer()
    facts = _golden_facts()
    if mutation == "missing":
        del facts["base_sha"]
    elif mutation == "extra":
        facts["unexpected"] = "value"
    else:
        resolved_links = facts["resolved_links"]
        assert isinstance(resolved_links, list)
        facts["resolved_links"] = list(reversed(resolved_links))

    # When / Then: no alternate genesis dialect is accepted.
    with pytest.raises(writer.LedgerError):
        writer.build_genesis(facts, clock=_fixed_clock)


def test_bootstrap_refuses_existing_ledger(tmp_path: Path) -> None:
    # Given: an existing ledger, even if it is empty.
    writer = _load_writer()
    ledger = tmp_path / "operations.ndjson"
    ledger.touch()

    # When / Then: bootstrap never recreates or truncates it.
    with pytest.raises(writer.LedgerError):
        writer.write_genesis(ledger, writer.build_genesis(_golden_facts(), clock=_fixed_clock))
    assert ledger.read_bytes() == b""


def test_parallel_writers_form_gap_free_hash_chain(tmp_path: Path) -> None:
    # Given: one valid genesis and 32 independent processes.
    writer = _load_writer()
    ledger = tmp_path / "operations.ndjson"
    writer.write_genesis(ledger, writer.build_genesis(_golden_facts(), clock=_fixed_clock))

    # When: all writers append concurrently.
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_append_worker, args=(str(WRITER_PATH), str(ledger), worker))
        for worker in range(32)
    ]
    started = processes[:0]
    try:
        for process in processes:
            process.start()
            started.append(process)
        deadline = time.monotonic() + 20
        for process in started:
            process.join(timeout=max(0, deadline - time.monotonic()))
    finally:
        for process in started:
            if process.is_alive():
                process.terminate()
        for process in started:
            process.join(timeout=1)
        for process in started:
            if process.is_alive():
                process.kill()
                process.join()

    # Then: every child succeeds and the ledger is one valid gap-free sequence.
    assert all(process.exitcode == 0 for process in processes)
    entries = writer.verify_ledger(ledger)
    assert [entry["sequence"] for entry in entries] == list(range(33))
    assert {entry["arguments"]["worker"] for entry in entries[1:]} == set(range(32))


def test_lock_open_failure_is_not_retried_or_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a valid ledger whose parent disappears at the lock-open boundary.
    writer = _load_writer()
    ledger = tmp_path / "operations.ndjson"
    writer.write_genesis(ledger, writer.build_genesis(_golden_facts(), clock=_fixed_clock))
    original = ledger.read_bytes()
    implementation = sys.modules[writer.append_operation.__module__]
    lock_open_calls = 0
    real_open = implementation.os.open

    def fail_lock_open(*args: object, **kwargs: object) -> int:
        nonlocal lock_open_calls
        if args and args[0] == ".evidence-writer.lock":
            lock_open_calls += 1
            raise FileNotFoundError
        return real_open(*args, **kwargs)

    monkeypatch.setattr(implementation.os, "open", fail_lock_open)

    # When / Then: the identity loss fails closed without retrying or changing bytes.
    with pytest.raises(writer.LedgerError, match="writer lock cannot be opened safely"):
        writer.append_operation(
            ledger,
            task_id="02",
            action_class="test",
            arguments={},
            status="passed",
        )
    assert lock_open_calls == 1
    assert ledger.read_bytes() == original


def test_append_rejects_wrong_previous_hash_and_seal(tmp_path: Path) -> None:
    # Given: a valid ledger whose current tip is known.
    writer = _load_writer()
    ledger = tmp_path / "operations.ndjson"
    genesis = writer.build_genesis(_golden_facts(), clock=_fixed_clock)
    writer.write_genesis(ledger, genesis)

    # When / Then: stale writers fail closed.
    with pytest.raises(writer.LedgerError):
        writer.append_operation(
            ledger,
            task_id="02",
            action_class="test",
            arguments={},
            status="passed",
            expected_previous_hash="0" * 64,
        )

    writer.append_operation(
        ledger,
        task_id="23",
        action_class="seal",
        arguments={},
        status="passed",
        expected_previous_hash=genesis["entry_hash"],
    )
    with pytest.raises(writer.LedgerError):
        writer.append_operation(
            ledger,
            task_id="02",
            action_class="test",
            arguments={},
            status="passed",
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"ratio": 0.5},
        {"api_key": "dummy-super-secret"},
        {"cookie": "dummy-cookie"},
        {"headers": {"Authorization": "Bearer dummy"}},
        {"request_body": "dummy-body"},
        {"path": "/Users/someone/private"},
    ],
)
def test_append_rejects_float_and_secret_or_path_payloads(
    tmp_path: Path, payload: dict[str, JSONValue]
) -> None:
    # Given: a valid ledger and unsafe evidence arguments.
    writer = _load_writer()
    ledger = tmp_path / "operations.ndjson"
    writer.write_genesis(ledger, writer.build_genesis(_golden_facts(), clock=_fixed_clock))

    # When / Then: unsafe evidence cannot be serialized.
    with pytest.raises(writer.LedgerError):
        writer.append_operation(
            ledger,
            task_id="02",
            action_class="test",
            arguments=payload,
            status="failed",
        )


@pytest.mark.parametrize(
    "corruption",
    [
        b'{"schema_version":1}\n',
    ],
)
def test_append_detects_complete_direct_write(tmp_path: Path, corruption: bytes) -> None:
    # Given: a valid prefix followed by bytes not produced by the writer.
    writer = _load_writer()
    ledger = tmp_path / "operations.ndjson"
    writer.write_genesis(ledger, writer.build_genesis(_golden_facts(), clock=_fixed_clock))
    with ledger.open("ab") as handle:
        handle.write(corruption)

    # When / Then: validation rejects the ledger without altering its bytes.
    before = ledger.read_bytes()
    with pytest.raises(writer.LedgerError):
        writer.append_operation(
            ledger,
            task_id="02",
            action_class="test",
            arguments={},
            status="passed",
        )
    assert ledger.read_bytes() == before


@pytest.mark.parametrize(
    ("stage", "expected_count"),
    [("before_write", 1), ("after_file_fsync", 2), ("after_parent_fsync", 2)],
)
def test_interruption_preserves_or_validly_advances_prefix(
    tmp_path: Path, stage: str, expected_count: int
) -> None:
    # Given: a valid prefix and an interruption hook at a durability boundary.
    writer = _load_writer()
    ledger = tmp_path / "operations.ndjson"
    writer.write_genesis(ledger, writer.build_genesis(_golden_facts(), clock=_fixed_clock))

    def interrupt(point: str) -> None:
        if point == stage:
            raise writer.InjectedInterruption(stage)

    # When: the append is interrupted.
    with pytest.raises(writer.InjectedInterruption):
        writer.append_operation(
            ledger,
            task_id="02",
            action_class="test",
            arguments={"stage": stage},
            status="failed",
            interruption_hook=interrupt,
        )

    # Then: the prior prefix is intact or has advanced by one complete valid entry.
    assert len(writer.verify_ledger(ledger)) == expected_count


def test_cli_has_no_timestamp_override() -> None:
    # Given: production CLI help is the public clock boundary.

    # When: help is requested.
    result = subprocess.run(
        [sys.executable, str(WRITER_PATH), "append", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: the clock cannot be supplied by a caller.
    assert result.returncode == 0
    assert "timestamp" not in result.stdout.lower()
