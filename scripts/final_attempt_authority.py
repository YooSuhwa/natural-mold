"""Read-only authority checks for consumers of the active final attempt."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from operation_ledger_chain import _verify_bytes
from operation_ledger_format import JSONValue, LedgerError, canonical_line
from operation_ledger_fs import (
    close,
    open_existing_at,
    open_parent,
    read_all,
    validate_name,
)
from operation_ledger_writer import _active_attempt_from_lifecycle


class FinalAttemptAuthorityError(RuntimeError):
    """The active final-attempt authority could not be proven."""


def _validate_root(evidence_root: Path, descriptor: int) -> tuple[int, int]:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o022
    ):
        raise FinalAttemptAuthorityError("evidence root descriptor is unsafe")
    try:
        rebound, _ = open_parent(evidence_root.absolute() / ".authority-anchor", create=False)
    except LedgerError as error:
        raise FinalAttemptAuthorityError("evidence root pathname cannot be trusted") from error
    try:
        named = os.fstat(rebound)
        identity = (metadata.st_dev, metadata.st_ino)
        if (named.st_dev, named.st_ino) != identity:
            raise FinalAttemptAuthorityError("evidence root pathname identity changed")
        return identity
    finally:
        os.close(rebound)


def _read_canonical_json(parent_fd: int, name: str) -> dict[str, JSONValue]:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except OSError as error:
        raise FinalAttemptAuthorityError(
            f"required authority file is unavailable: {name}"
        ) from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o022
        ):
            raise FinalAttemptAuthorityError(f"authority file is unsafe: {name}")
        chunks: list[bytes] = []
        size = 0
        while chunk := os.read(descriptor, 64 * 1024):
            size += len(chunk)
            if size > 1024 * 1024:
                raise FinalAttemptAuthorityError(f"authority file is too large: {name}")
            chunks.append(chunk)
        payload = b"".join(chunks)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            (named.st_dev, named.st_ino) != (metadata.st_dev, metadata.st_ino)
            or named.st_uid != os.geteuid()
            or named.st_nlink != 1
            or not stat.S_ISREG(named.st_mode)
            or named.st_mode & 0o022
        ):
            raise FinalAttemptAuthorityError(f"authority file identity changed: {name}")
        value = json.loads(payload)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise FinalAttemptAuthorityError(f"authority file cannot be trusted: {name}") from error
    finally:
        os.close(descriptor)
    if not isinstance(value, dict) or canonical_line(value) != payload:
        raise FinalAttemptAuthorityError(f"authority file is not canonical: {name}")
    return value


def _reject_nonterminal_journals(evidence_root_fd: int) -> None:
    try:
        directory = os.open(
            "lifecycle-journals",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=evidence_root_fd,
        )
    except FileNotFoundError:
        return
    try:
        metadata = os.fstat(directory)
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
            raise FinalAttemptAuthorityError("lifecycle journal directory is unsafe")
        for name in sorted(
            os.listdir(directory)  # noqa: PTH208 - descriptor-bound enumeration
        ):
            if not name.endswith(".json"):
                raise FinalAttemptAuthorityError("lifecycle journal namespace is unsafe")
            journal = _read_canonical_json(directory, name)
            if journal.get("phase") != "committed":
                raise FinalAttemptAuthorityError("a lifecycle journal is nonterminal")
        named = os.stat(
            "lifecycle-journals",
            dir_fd=evidence_root_fd,
            follow_symlinks=False,
        )
        if (
            (named.st_dev, named.st_ino) != (metadata.st_dev, metadata.st_ino)
            or not stat.S_ISDIR(named.st_mode)
            or named.st_uid != os.geteuid()
            or named.st_mode & 0o022
        ):
            raise FinalAttemptAuthorityError("lifecycle journal directory identity changed")
    finally:
        os.close(directory)


def verify_open_final_attempt_authority(
    *,
    evidence_root: Path,
    evidence_root_fd: int,
    expected_attempt_id: str,
    expected_attempt_path: Path,
    expected_head: str,
) -> dict[str, JSONValue]:
    """Prove an exact canonical open pointer and its active ledger lifecycle relation."""
    root = evidence_root.absolute()
    root_identity = _validate_root(root, evidence_root_fd)
    expected_path = expected_attempt_path.absolute()
    if expected_path != root / "final-attempts" / expected_attempt_id:
        raise FinalAttemptAuthorityError("expected attempt path is outside the active namespace")
    try:
        repo_root = root.parents[2]
        relative_attempt = expected_path.relative_to(repo_root).as_posix()
    except (IndexError, ValueError) as error:
        raise FinalAttemptAuthorityError(
            "expected attempt path is outside the repository"
        ) from error
    expected_pointer: dict[str, JSONValue] = {
        "schema_version": 1,
        "attempt_id": expected_attempt_id,
        "attempt_dir": relative_attempt,
        "status": "open",
        "head": expected_head,
    }
    pointer = _read_canonical_json(evidence_root_fd, "current-final-attempt.json")
    if pointer != expected_pointer:
        raise FinalAttemptAuthorityError("open final-attempt pointer is not exact")
    _reject_nonterminal_journals(evidence_root_fd)
    try:
        bound = open_existing_at(evidence_root_fd, root, root / "operations.ndjson")
    except LedgerError as error:
        raise FinalAttemptAuthorityError("verified operations ledger is unavailable") from error
    try:
        entries = _verify_bytes(read_all(bound))
        if any(
            entry.get("action_class") == "seal" and entry.get("status") == "passed"
            for entry in entries
        ):
            raise FinalAttemptAuthorityError("operations ledger is externally sealed")
        active_attempt = _active_attempt_from_lifecycle(entries)
        validate_name(bound)
    except LedgerError as error:
        raise FinalAttemptAuthorityError("operations lifecycle relation is invalid") from error
    finally:
        close(bound)
    if active_attempt != expected_attempt_id:
        raise FinalAttemptAuthorityError("operations ledger has no matching active attempt")
    start_events = [
        entry
        for entry in entries
        if entry.get("action_class") == "final_attempt_started"
        and entry.get("status") == "passed"
        and isinstance(entry.get("arguments"), dict)
        and entry["arguments"].get("attempt_id") == expected_attempt_id
    ]
    if len(start_events) != 1 or start_events[0]["arguments"].get("head") != expected_head:
        raise FinalAttemptAuthorityError("active lifecycle relation is bound to another HEAD")
    _reject_nonterminal_journals(evidence_root_fd)
    if _read_canonical_json(evidence_root_fd, "current-final-attempt.json") != expected_pointer:
        raise FinalAttemptAuthorityError("open final-attempt pointer changed during verification")
    if (os.fstat(evidence_root_fd).st_dev, os.fstat(evidence_root_fd).st_ino) != root_identity:
        raise FinalAttemptAuthorityError("evidence root descriptor identity changed")
    _validate_root(root, evidence_root_fd)
    return pointer


__all__ = ["FinalAttemptAuthorityError", "verify_open_final_attempt_authority"]
