"""Read-only verification for the operations ledger hash chain."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from operation_ledger_format import (
    ENTRY_KEYS,
    HASH_PATTERN,
    IDENTIFIER_PATTERN,
    TIME_PATTERN,
    JSONValue,
    LedgerError,
    _validate_safe_arguments,
    canonical_line,
)
from operation_ledger_genesis import _validate_genesis_facts


def _validate_entry(
    entry: dict[str, JSONValue], *, expected_sequence: int, previous_hash: str | None
) -> None:
    if set(entry) != ENTRY_KEYS or entry.get("schema_version") != 1:
        raise LedgerError("ledger entry schema is invalid")
    if (
        entry.get("sequence") != expected_sequence
        or entry.get("previous_entry_hash") != previous_hash
    ):
        raise LedgerError("ledger sequence or previous hash is invalid")
    timestamp = entry.get("timestamp_utc")
    if not isinstance(timestamp, str) or TIME_PATTERN.fullmatch(timestamp) is None:
        raise LedgerError("ledger timestamp is invalid")
    for key in ("task_id", "action_class", "status"):
        value = entry.get(key)
        if not isinstance(value, str) or IDENTIFIER_PATTERN.fullmatch(value) is None:
            raise LedgerError("ledger identifier is invalid")
    digest = entry.get("entry_hash")
    if not isinstance(digest, str) or HASH_PATTERN.fullmatch(digest) is None:
        raise LedgerError("entry hash is invalid")
    unhashed = {key: value for key, value in entry.items() if key != "entry_hash"}
    if hashlib.sha256(canonical_line(unhashed)).hexdigest() != digest:
        raise LedgerError("entry hash verification failed")
    arguments = entry.get("arguments")
    if not isinstance(arguments, dict):
        raise LedgerError("ledger arguments must be an object")
    if expected_sequence == 0:
        _validate_genesis_facts(arguments)
        if entry.get("action_class") != "bootstrap" or entry.get("task_id") != "02":
            raise LedgerError("sequence zero must be the Todo 02 bootstrap")
    else:
        _validate_safe_arguments(arguments)


def _decode_line(line: bytes) -> dict[str, JSONValue]:
    if not line.endswith(b"\n"):
        raise LedgerError("ledger contains a torn line")
    try:
        parsed = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise LedgerError("ledger contains malformed JSON") from error
    if not isinstance(parsed, dict) or canonical_line(parsed) != line:
        raise LedgerError("ledger line is not canonical")
    return parsed


def _verify_bytes(data: bytes) -> list[dict[str, JSONValue]]:
    if not data:
        raise LedgerError("ledger is empty")
    entries: list[dict[str, JSONValue]] = []
    previous_hash: str | None = None
    for sequence, line in enumerate(data.splitlines(keepends=True)):
        entry = _decode_line(line)
        _validate_entry(entry, expected_sequence=sequence, previous_hash=previous_hash)
        entries.append(entry)
        digest = entry["entry_hash"]
        if not isinstance(digest, str):
            raise LedgerError("entry hash is invalid")
        previous_hash = digest
    return entries


def verify_ledger(path: Path) -> list[dict[str, JSONValue]]:
    """Validate the full canonical hash chain and return its entries."""
    return _verify_bytes(path.read_bytes())
