"""Filesystem identity and failure-atomicity tests for operation ledger writes."""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

type JSONValue = None | bool | int | str | list[JSONValue] | dict[str, JSONValue]

REPO_ROOT = Path(__file__).resolve().parents[2]
WRITER_PATH = REPO_ROOT / "scripts" / "append-operation.py"


@contextmanager
def _writer_path() -> Iterator[None]:
    original = sys.path[:]
    sys.path.insert(0, str(WRITER_PATH.parent))
    try:
        yield
    finally:
        sys.path[:] = original


def _load_writer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("operation_durability_test", WRITER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with _writer_path():
        spec.loader.exec_module(module)
    return module


def _facts() -> dict[str, JSONValue]:
    return {
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


def _ledger(writer: ModuleType, path: Path) -> Path:
    writer.write_genesis(
        path,
        writer.build_genesis(_facts(), clock=lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)),
    )
    return path


def _append(writer: ModuleType, ledger: Path, **extra: JSONValue) -> None:
    writer.append_operation(
        ledger,
        task_id="02",
        action_class="durability_test",
        arguments={"case": "fixture"},
        status="passed",
        **extra,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"note": "request Authorization: Bearer DUMMY_VALUE"},
        {"note": "Proxy-Authorization: Basic ZHVtbXk="},
        {"note": "-----BEGIN PRIVATE KEY-----\nDUMMY\n-----END PRIVATE KEY-----"},
        {"output_path": "../outside"},
        {"output_path": "C:\\outside\\file"},
        {"note": "path=folder/../../outside"},
    ],
)
def test_public_append_rejects_embedded_credentials_private_keys_and_traversal(
    tmp_path: Path, payload: dict[str, JSONValue]
) -> None:
    # Given: a valid ledger and one unsafe public API payload.
    writer = _load_writer()
    ledger = _ledger(writer, tmp_path / "operations.ndjson")

    # When / Then: recursive validation rejects it before bytes change.
    before = ledger.read_bytes()
    with pytest.raises(writer.LedgerError):
        writer.append_operation(
            ledger,
            task_id="02",
            action_class="security_test",
            arguments=payload,
            status="failed",
        )
    assert ledger.read_bytes() == before


def test_public_append_preserves_safe_path_and_authorization_prose(tmp_path: Path) -> None:
    # Given: safe prose without credential or filesystem value syntax.
    writer = _load_writer()
    ledger = _ledger(writer, tmp_path / "operations.ndjson")

    # When: it is appended through the public API.
    writer.append_operation(
        ledger,
        task_id="02",
        action_class="security_test",
        arguments={"note": "Authorization handling and relative paths were reviewed."},
        status="passed",
    )

    # Then: the chain advances normally.
    assert len(writer.verify_ledger(ledger)) == 2


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "intermediate_symlink"])
def test_append_rejects_unbound_ledger_path_without_changing_target(
    tmp_path: Path, kind: str
) -> None:
    # Given: a valid target reached through an unsafe filesystem identity.
    writer = _load_writer()
    target = _ledger(writer, tmp_path / "target.ndjson")
    ledger = tmp_path / "operations.ndjson"
    if kind == "symlink":
        ledger.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, ledger)
    else:
        real_parent = tmp_path / "real-parent"
        real_parent.mkdir()
        target.rename(real_parent / ledger.name)
        target = real_parent / ledger.name
        linked_parent = tmp_path / "linked-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        ledger = linked_parent / ledger.name
    before = target.read_bytes()

    # When / Then: append fails before touching the linked target.
    with pytest.raises(writer.LedgerError):
        _append(writer, ledger)
    assert target.read_bytes() == before


def test_append_writes_all_short_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the OS accepts at most seven bytes per write.
    writer = _load_writer()
    ledger = _ledger(writer, tmp_path / "operations.ndjson")
    original_write = os.write
    calls = 0

    def short_write(descriptor: int, data: bytes) -> int:
        nonlocal calls
        calls += 1
        return original_write(descriptor, data[:7])

    monkeypatch.setattr(os, "write", short_write)

    # When: one append is committed.
    _append(writer, ledger)

    # Then: repeated writes complete one valid entry.
    assert calls > 1
    assert len(writer.verify_ledger(ledger)) == 2


@pytest.mark.parametrize("failure", ["error", "zero"])
def test_append_rolls_back_partial_or_zero_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    # Given: a valid prefix and an OS write that cannot finish the new entry.
    writer = _load_writer()
    ledger = _ledger(writer, tmp_path / "operations.ndjson")
    before = ledger.read_bytes()
    original_write = os.write
    calls = 0

    def failing_write(descriptor: int, data: bytes) -> int:
        nonlocal calls
        calls += 1
        if failure == "zero":
            return 0
        if calls == 1:
            return original_write(descriptor, data[:7])
        raise OSError("injected write failure")

    monkeypatch.setattr(os, "write", failing_write)

    # When / Then: append fails and restores the exact verified prefix.
    with pytest.raises((writer.LedgerError, OSError)):
        _append(writer, ledger)
    assert ledger.read_bytes() == before
    assert len(writer.verify_ledger(ledger)) == 1


def test_genesis_write_failure_leaves_no_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a new ledger whose first write is partial.
    writer = _load_writer()
    ledger = tmp_path / "operations.ndjson"
    monkeypatch.setattr(os, "write", lambda descriptor, data: 0)

    # When / Then: creation fails and removes only its bound inode.
    with pytest.raises(writer.LedgerError):
        writer.write_genesis(
            ledger,
            writer.build_genesis(_facts(), clock=lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)),
        )
    assert not ledger.exists()


def test_append_recovers_only_unterminated_final_suffix(tmp_path: Path) -> None:
    # Given: a valid newline prefix followed by an unterminated torn suffix.
    writer = _load_writer()
    ledger = _ledger(writer, tmp_path / "operations.ndjson")
    with ledger.open("ab") as handle:
        handle.write(b'{"torn":')

    # When: the writer opens the ledger under its exclusive lock.
    _append(writer, ledger)

    # Then: only the suffix is discarded and a valid entry advances the prefix.
    assert len(writer.verify_ledger(ledger)) == 2


def test_append_never_repairs_complete_invalid_line(tmp_path: Path) -> None:
    # Given: a complete LF-terminated malformed line after a valid prefix.
    writer = _load_writer()
    ledger = _ledger(writer, tmp_path / "operations.ndjson")
    with ledger.open("ab") as handle:
        handle.write(b'{"invalid":true}\n')
    before = ledger.read_bytes()

    # When / Then: append rejects it byte-for-byte.
    with pytest.raises(writer.LedgerError):
        _append(writer, ledger)
    assert ledger.read_bytes() == before


def test_append_detects_valid_ledger_name_swap_before_commit(tmp_path: Path) -> None:
    # Given: two valid ledgers and a deterministic pre-commit pathname swap.
    writer = _load_writer()
    ledger = _ledger(writer, tmp_path / "operations.ndjson")
    alternate = _ledger(writer, tmp_path / "alternate.ndjson")
    alternate_bytes = alternate.read_bytes()

    def swap(stage: str) -> None:
        if stage == "before_commit":
            alternate.replace(ledger)

    # When / Then: identity validation rejects the swap and leaves the named target unchanged.
    with pytest.raises(writer.LedgerError):
        writer.append_operation(
            ledger,
            task_id="02",
            action_class="durability_test",
            arguments={"case": "swap"},
            status="failed",
            filesystem_hook=swap,
        )
    assert ledger.read_bytes() == alternate_bytes


def test_post_file_fsync_validation_failure_rolls_back_and_retry_appends_once(
    tmp_path: Path,
) -> None:
    # Given: a valid ledger and a post-durability hook that makes its name unsafe.
    writer = _load_writer()
    ledger = _ledger(writer, tmp_path / "operations.ndjson")
    before = ledger.read_bytes()

    def make_ledger_writable(stage: str) -> None:
        if stage == "after_file_fsync":
            ledger.chmod(0o660)

    # When: validation runs after the new entry reaches durable file storage.
    with pytest.raises(writer.LedgerError):
        _append(writer, ledger, interruption_hook=make_ledger_writable)
    ledger.chmod(0o600)

    # Then: rollback restores exactly the verified prefix and a retry adds one entry.
    assert ledger.read_bytes() == before
    _append(writer, ledger)
    assert len(writer.verify_ledger(ledger)) == 2


def test_post_file_fsync_parent_fsync_failure_rolls_back_and_retry_appends_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a valid ledger and an injected failure on the append parent fsync.
    writer = _load_writer()
    ledger = _ledger(writer, tmp_path / "operations.ndjson")
    before = ledger.read_bytes()
    original_fsync = os.fsync
    calls = 0

    def fail_parent_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected parent fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_parent_fsync)

    # When: the parent fsync reports failure after the file has been synced.
    with pytest.raises(OSError, match="parent fsync"):
        _append(writer, ledger)

    # Then: rollback is durable and retry does not duplicate the failed append.
    assert ledger.read_bytes() == before
    monkeypatch.setattr(os, "fsync", original_fsync)
    _append(writer, ledger)
    assert len(writer.verify_ledger(ledger)) == 2


def test_append_detects_post_parent_fsync_name_swap_without_touching_replacement(
    tmp_path: Path,
) -> None:
    # Given: a valid append and a foreign valid ledger swapped in after parent fsync.
    writer = _load_writer()
    ledger = _ledger(writer, tmp_path / "operations.ndjson")
    alternate = _ledger(writer, tmp_path / "alternate.ndjson")
    alternate_bytes = alternate.read_bytes()

    def swap_after_parent_fsync(stage: str) -> None:
        if stage == "after_parent_fsync":
            alternate.replace(ledger)

    # When / Then: the final fd/name proof fails and rollback touches only the opened inode.
    with pytest.raises(writer.LedgerError, match="pathname identity changed"):
        _append(writer, ledger, interruption_hook=swap_after_parent_fsync)
    assert ledger.read_bytes() == alternate_bytes
