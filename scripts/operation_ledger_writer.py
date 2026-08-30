"""Exclusive, durable write transactions for the operations ledger."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from operation_ledger_chain import _validate_entry, _verify_bytes
from operation_ledger_format import (
    Clock,
    InterruptionHook,
    JSONValue,
    LedgerError,
    _timestamp,
    _validate_safe_arguments,
    _with_hash,
    canonical_line,
)
from operation_ledger_fs import (
    BoundLedger,
    close,
    create_exclusive,
    open_existing,
    read_all,
    remove_created,
    rollback,
    validate_name,
    write_all,
)

type FilesystemHook = Callable[[str], None]


def _recover_torn_suffix(bound: BoundLedger, data: bytes) -> bytes:
    if data.endswith(b"\n"):
        return data
    prefix_end = data.rfind(b"\n") + 1
    if prefix_end == 0:
        raise LedgerError("ledger contains no valid newline prefix")
    prefix = data[:prefix_end]
    _verify_bytes(prefix)
    rollback(bound, prefix_end)
    return prefix


def write_genesis(path: Path, entry: Mapping[str, JSONValue]) -> None:
    """Create a new ledger without replacing any existing filesystem entry."""
    _validate_entry(dict(entry), expected_sequence=0, previous_hash=None)
    bound = create_exclusive(path)
    try:
        line = canonical_line(entry)
        write_all(bound.file_fd, line)
        os.fsync(bound.file_fd)
        validate_name(bound)
        os.fsync(bound.parent_fd)
    except (LedgerError, OSError):
        remove_created(bound)
        raise
    finally:
        close(bound)


def append_operation(
    path: Path,
    *,
    task_id: str,
    action_class: str,
    arguments: Mapping[str, JSONValue],
    status: str,
    expected_previous_hash: str | None = None,
    clock: Clock = lambda: datetime.now(UTC),
    interruption_hook: InterruptionHook | None = None,
    filesystem_hook: FilesystemHook | None = None,
) -> dict[str, JSONValue]:
    """Lock, verify, and durably append exactly one bounded operation."""
    _validate_safe_arguments(arguments)
    bound = open_existing(path)
    try:
        fcntl.flock(bound.file_fd, fcntl.LOCK_EX)
        data = _recover_torn_suffix(bound, read_all(bound))
        entries = _verify_bytes(data)
        tip = entries[-1]
        if tip["action_class"] == "seal" and tip["status"] == "passed":
            raise LedgerError("ledger is sealed")
        previous_hash = tip["entry_hash"]
        if not isinstance(previous_hash, str):
            raise LedgerError("ledger tip hash is invalid")
        if expected_previous_hash is not None and expected_previous_hash != previous_hash:
            raise LedgerError("expected previous hash does not match the ledger tip")
        entry = _with_hash(
            {
                "schema_version": 1,
                "sequence": len(entries),
                "previous_entry_hash": previous_hash,
                "timestamp_utc": _timestamp(clock),
                "task_id": task_id,
                "action_class": action_class,
                "arguments": dict(arguments),
                "status": status,
            }
        )
        _validate_entry(entry, expected_sequence=len(entries), previous_hash=previous_hash)
        if interruption_hook is not None:
            interruption_hook("before_write")
        if filesystem_hook is not None:
            filesystem_hook("before_commit")
        validate_name(bound)
        verified_eof = len(data)
        os.lseek(bound.file_fd, verified_eof, os.SEEK_SET)
        line = canonical_line(entry)
        try:
            write_all(bound.file_fd, line)
        except (LedgerError, OSError):
            rollback(bound, verified_eof)
            raise
        os.fsync(bound.file_fd)
        if interruption_hook is not None:
            interruption_hook("after_file_fsync")
        validate_name(bound)
        os.fsync(bound.parent_fd)
        if interruption_hook is not None:
            interruption_hook("after_parent_fsync")
        return entry
    finally:
        close(bound)
