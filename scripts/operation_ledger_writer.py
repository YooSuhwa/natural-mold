"""Exclusive, durable write transactions for the operations ledger."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
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
    open_existing_at,
    open_parent,
    read_all,
    remove_created,
    rollback,
    validate_name,
    write_all,
)

type FilesystemHook = Callable[[str], None]
type LifecycleRevalidation = Callable[[], None]

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
LIFECYCLE_ACTIONS = {
    "final_attempt_started",
    "final_attempt_abandoned",
    "seal_reopened",
}
JOURNAL_PHASES = {
    "begin": {"prepared", "ledger_appended", "directory_created", "pointer_open", "committed"},
    "abandon": {
        "prepared",
        "ledger_appended",
        "terminal_written",
        "pointer_abandoned",
        "committed",
    },
    "seal": {"prepared", "seal_written", "pointer_sealed", "committed"},
    "reopen": {"prepared", "ledger_appended", "pointer_reopened", "committed"},
}


def _valid_inventory(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"files", "sha256"}:
        return False
    digest = value.get("sha256")
    files = value.get("files")
    if (
        not isinstance(digest, str)
        or SHA64.fullmatch(digest) is None
        or not isinstance(files, list)
    ):
        return False
    paths: list[str] = []
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size"}:
            return False
        path = entry.get("path")
        file_hash = entry.get("sha256")
        size = entry.get("size")
        if (
            not isinstance(path, str)
            or not path
            or Path(path).is_absolute()
            or ".." in Path(path).parts
            or not isinstance(file_hash, str)
            or SHA64.fullmatch(file_hash) is None
            or type(size) is not int
            or size < 0
        ):
            return False
        paths.append(path)
    return (
        paths == sorted(set(paths))
        and digest == hashlib.sha256(canonical_line({"files": files})).hexdigest()
    )


@dataclass(frozen=True, slots=True)
class EvidenceWriterLock:
    """An exclusive lock bound to one evidence directory."""

    descriptor: int
    parent_descriptor: int
    evidence_root: Path
    file_identity: tuple[int, int]


_CURRENT_EVIDENCE_LOCK: ContextVar[EvidenceWriterLock | None] = ContextVar(
    "current_evidence_lock", default=None
)


def current_evidence_lock() -> EvidenceWriterLock | None:
    """Return the evidence-root binding active in this execution context."""
    return _CURRENT_EVIDENCE_LOCK.get()


@contextmanager
def evidence_writer_lock(path: Path) -> Iterator[EvidenceWriterLock]:
    """Serialize every ledger/pointer/journal/seal mutation in one evidence root."""
    absolute = path.absolute()
    evidence_root = absolute.parent
    parent_descriptor, _ = open_parent(absolute, create=False)
    try:
        descriptor = os.open(
            ".evidence-writer.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent_descriptor,
        )
    except OSError as error:
        os.close(parent_descriptor)
        raise LedgerError("evidence writer lock cannot be opened safely") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o022
        ):
            raise LedgerError("evidence writer lock identity is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        lock = EvidenceWriterLock(
            descriptor,
            parent_descriptor,
            evidence_root,
            (metadata.st_dev, metadata.st_ino),
        )
        _validate_writer_lock(lock)
        token = _CURRENT_EVIDENCE_LOCK.set(lock)
        try:
            yield lock
        finally:
            _CURRENT_EVIDENCE_LOCK.reset(token)
    finally:
        os.close(descriptor)
        os.close(parent_descriptor)


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
    with evidence_writer_lock(path) as evidence_lock:
        return append_operation_locked(
            path,
            evidence_lock=evidence_lock,
            task_id=task_id,
            action_class=action_class,
            arguments=arguments,
            status=status,
            expected_previous_hash=expected_previous_hash,
            clock=clock,
            interruption_hook=interruption_hook,
            filesystem_hook=filesystem_hook,
        )


def append_operation_locked(
    path: Path,
    *,
    evidence_lock: EvidenceWriterLock,
    task_id: str,
    action_class: str,
    arguments: Mapping[str, JSONValue],
    status: str,
    expected_previous_hash: str | None = None,
    clock: Clock = lambda: datetime.now(UTC),
    interruption_hook: InterruptionHook | None = None,
    filesystem_hook: FilesystemHook | None = None,
) -> dict[str, JSONValue]:
    """Append an ordinary operation while the caller holds the writer lock."""
    return _append_operation_bound(
        path,
        evidence_lock=evidence_lock,
        task_id=task_id,
        action_class=action_class,
        arguments=arguments,
        status=status,
        expected_previous_hash=expected_previous_hash,
        clock=clock,
        interruption_hook=interruption_hook,
        filesystem_hook=filesystem_hook,
        lifecycle_revalidation=None,
    )


def _append_final_attempt_lifecycle_operation(
    path: Path,
    *,
    evidence_lock: EvidenceWriterLock,
    expected_previous_hash: str,
    revalidate: LifecycleRevalidation,
) -> dict[str, JSONValue]:
    """Append the pending transition only after its owner revalidates exact inputs."""
    pending_journal = _pending_lifecycle_journal(evidence_lock)
    action_class, arguments = _expected_journal_event(pending_journal or {})
    return _append_operation_bound(
        path,
        evidence_lock=evidence_lock,
        task_id="final-attempt",
        action_class=action_class,
        arguments=arguments,
        status="passed",
        expected_previous_hash=expected_previous_hash,
        lifecycle_revalidation=revalidate,
    )


def _append_operation_bound(
    path: Path,
    *,
    evidence_lock: EvidenceWriterLock,
    task_id: str,
    action_class: str,
    arguments: Mapping[str, JSONValue],
    status: str,
    expected_previous_hash: str | None = None,
    clock: Clock = lambda: datetime.now(UTC),
    interruption_hook: InterruptionHook | None = None,
    filesystem_hook: FilesystemHook | None = None,
    lifecycle_revalidation: LifecycleRevalidation | None,
) -> dict[str, JSONValue]:
    """Append one operation after selecting the ordinary or private lifecycle surface."""
    if evidence_lock.evidence_root != path.absolute().parent:
        raise LedgerError("evidence writer lock is bound to another root")
    lifecycle_resume = lifecycle_revalidation is not None
    if action_class in LIFECYCLE_ACTIONS and not lifecycle_resume:
        raise LedgerError(
            "final-attempt lifecycle actions require the private transition capability"
        )
    if action_class not in LIFECYCLE_ACTIONS and lifecycle_resume:
        raise LedgerError("private transition capability cannot authorize an ordinary operation")
    _validate_safe_arguments(arguments)
    _validate_writer_lock(evidence_lock)
    _revalidate_evidence_root(evidence_lock)
    bound = open_existing_at(evidence_lock.parent_descriptor, evidence_lock.evidence_root, path)
    try:
        fcntl.flock(bound.file_fd, fcntl.LOCK_EX)
        data = _recover_torn_suffix(bound, read_all(bound))
        entries = _verify_bytes(data)
        _reject_external_seal(
            evidence_lock,
            entries=entries,
            action_class=action_class,
            arguments=arguments,
            status=status,
            lifecycle_resume=lifecycle_resume,
        )
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
        if lifecycle_revalidation is not None:
            lifecycle_revalidation()
        _validate_writer_lock(evidence_lock)
        validate_name(bound)
        _revalidate_evidence_root(evidence_lock)
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
        try:
            _validate_writer_lock(evidence_lock)
            validate_name(bound)
            _revalidate_evidence_root(evidence_lock)
            os.fsync(bound.parent_fd)
        except (LedgerError, OSError):
            rollback(bound, verified_eof)
            raise
        if interruption_hook is not None:
            interruption_hook("after_parent_fsync")
        try:
            _validate_writer_lock(evidence_lock)
            validate_name(bound)
            _revalidate_evidence_root(evidence_lock)
        except (LedgerError, OSError):
            rollback(bound, verified_eof)
            raise
        return entry
    finally:
        close(bound)


def _reject_external_seal(
    evidence_lock: EvidenceWriterLock,
    *,
    entries: list[dict[str, JSONValue]],
    action_class: str,
    arguments: Mapping[str, JSONValue],
    status: str,
    lifecycle_resume: bool,
) -> None:
    active_attempt = _active_attempt_from_lifecycle(entries)
    pending_journal = _pending_lifecycle_journal(evidence_lock)
    if pending_journal is not None and not lifecycle_resume:
        raise LedgerError("a final-attempt lifecycle transition is nonterminal")
    if lifecycle_resume:
        _validate_private_lifecycle_transition(
            pending_journal,
            action_class=action_class,
            arguments=arguments,
            status=status,
        )
    try:
        descriptor = os.open(
            "current-final-attempt.json",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=evidence_lock.parent_descriptor,
        )
    except FileNotFoundError:
        if active_attempt is not None:
            raise LedgerError("final-attempt pointer is missing for an active attempt") from None
        return
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o022
        ):
            raise LedgerError("final-attempt pointer cannot be trusted")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 64 * 1024):
            total += len(chunk)
            if total > 1024 * 1024:
                raise LedgerError("final-attempt pointer is too large")
            chunks.append(chunk)
        payload = b"".join(chunks)
        pointer = json.loads(payload)
        named = os.stat(
            "current-final-attempt.json",
            dir_fd=evidence_lock.parent_descriptor,
            follow_symlinks=False,
        )
        if (
            (named.st_dev, named.st_ino) != (metadata.st_dev, metadata.st_ino)
            or named.st_uid != os.geteuid()
            or named.st_nlink != 1
            or not stat.S_ISREG(named.st_mode)
            or named.st_mode & 0o022
        ):
            raise LedgerError("final-attempt pointer identity changed")
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise LedgerError("final-attempt pointer cannot be trusted") from error
    finally:
        os.close(descriptor)
    if not isinstance(pointer, dict):
        raise LedgerError("final-attempt pointer cannot be trusted")
    status = pointer.get("status")
    base = {"schema_version", "attempt_id", "attempt_dir", "status", "head"}
    extras = {
        "open": set(),
        "abandoned": {"transition_id", "frozen_inventory"},
        "sealed": {"seal_id", "seal_sha256"},
        "reopened": {"transition_id", "frozen_inventory", "seal_id", "seal_sha256"},
    }
    if (
        status not in extras
        or set(pointer) != base | extras[str(status)]
        or canonical_line(pointer) != payload
        or pointer.get("schema_version") != 1
        or not isinstance(pointer.get("attempt_id"), str)
        or SHA64.fullmatch(str(pointer["attempt_id"])) is None
        or not isinstance(pointer.get("head"), str)
        or SHA40.fullmatch(str(pointer["head"])) is None
        or not isinstance(pointer.get("attempt_dir"), str)
        or Path(str(pointer["attempt_dir"])).is_absolute()
        or ".." in Path(str(pointer["attempt_dir"])).parts
        or Path(str(pointer["attempt_dir"])).parts[-2:]
        != ("final-attempts", str(pointer["attempt_id"]))
    ):
        raise LedgerError("final-attempt pointer cannot be trusted")
    for field in ("transition_id", "seal_id", "seal_sha256"):
        if field in pointer and (
            not isinstance(pointer[field], str) or SHA64.fullmatch(str(pointer[field])) is None
        ):
            raise LedgerError("final-attempt pointer values are invalid")
    if "frozen_inventory" in pointer and not _valid_inventory(pointer["frozen_inventory"]):
        raise LedgerError("final-attempt pointer inventory is invalid")
    pointer_attempt = pointer["attempt_id"]
    pointer_status = pointer["status"]
    if pointer_status in {"open", "sealed"} and active_attempt != pointer_attempt:
        raise LedgerError("final-attempt pointer disagrees with lifecycle relations")
    if pointer_status in {"abandoned", "reopened"} and active_attempt is not None:
        raise LedgerError("final-attempt pointer disagrees with lifecycle relations")
    if pointer.get("status") == "sealed" and not (
        lifecycle_resume and action_class == "seal_reopened"
    ):
        raise LedgerError("operations ledger is externally sealed")


def _active_attempt_from_lifecycle(entries: list[dict[str, JSONValue]]) -> str | None:
    """Derive the sole active attempt, rejecting ambiguous historic lifecycle events."""
    active_attempt: str | None = None
    seen_attempts: set[str] = set()
    for entry in entries:
        action = entry["action_class"]
        if action not in LIFECYCLE_ACTIONS:
            continue
        if entry["status"] != "passed":
            raise LedgerError("final-attempt lifecycle relation is invalid")
        value = entry["arguments"]
        if not isinstance(value, dict):
            raise LedgerError("final-attempt lifecycle relation is invalid")
        attempt_id = value.get("attempt_id")
        if not isinstance(attempt_id, str) or SHA64.fullmatch(attempt_id) is None:
            raise LedgerError("final-attempt lifecycle relation is invalid")
        if action == "final_attempt_started":
            if set(value) != {"attempt_id", "head"}:
                raise LedgerError("final-attempt lifecycle relation is invalid")
            head = value.get("head")
            if not isinstance(head, str) or SHA40.fullmatch(head) is None:
                raise LedgerError("final-attempt lifecycle relation is invalid")
            if active_attempt is not None:
                raise LedgerError("final-attempt lifecycle relation is ambiguous")
            if attempt_id in seen_attempts:
                raise LedgerError("duplicate final-attempt lifecycle relation")
            seen_attempts.add(attempt_id)
            active_attempt = attempt_id
            continue
        transition_key = "abandon_id" if action == "final_attempt_abandoned" else "reopen_id"
        if set(value) != {transition_key, "attempt_id", "failure_path", "failure_sha256"}:
            raise LedgerError("final-attempt lifecycle relation is invalid")
        transition_id = value.get(transition_key)
        failure_sha = value.get("failure_sha256")
        failure_path = value.get("failure_path")
        if (
            not isinstance(transition_id, str)
            or SHA64.fullmatch(transition_id) is None
            or not isinstance(failure_sha, str)
            or SHA64.fullmatch(failure_sha) is None
            or not isinstance(failure_path, str)
            or not failure_path
        ):
            raise LedgerError("final-attempt lifecycle relation is invalid")
        if active_attempt != attempt_id:
            raise LedgerError("final-attempt lifecycle relation is invalid")
        active_attempt = None
    return active_attempt


def _pending_lifecycle_journal(evidence_lock: EvidenceWriterLock) -> dict[str, JSONValue] | None:
    """Read every trusted journal and return the only nonterminal transition, if any."""
    try:
        directory = os.open(
            "lifecycle-journals",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=evidence_lock.parent_descriptor,
        )
    except FileNotFoundError:
        return None
    try:
        metadata = os.fstat(directory)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o022
        ):
            raise LedgerError("lifecycle journal directory is unsafe")
        pending: dict[str, JSONValue] | None = None
        for name in sorted(
            os.listdir(directory)  # noqa: PTH208 - descriptor-bound enumeration
        ):
            if not name.endswith(".json"):
                raise LedgerError("lifecycle journal namespace is unsafe")
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory,
            )
            try:
                entry_metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(entry_metadata.st_mode)
                    or entry_metadata.st_uid != os.geteuid()
                    or entry_metadata.st_nlink != 1
                    or entry_metadata.st_mode & 0o022
                ):
                    raise LedgerError("lifecycle journal is unsafe")
                payload = b""
                while chunk := os.read(descriptor, 64 * 1024):
                    payload += chunk
                    if len(payload) > 1024 * 1024:
                        raise LedgerError("lifecycle journal is too large")
                value = json.loads(payload)
                named = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if (
                    (named.st_dev, named.st_ino) != (entry_metadata.st_dev, entry_metadata.st_ino)
                    or not stat.S_ISREG(named.st_mode)
                    or named.st_uid != os.geteuid()
                    or named.st_nlink != 1
                    or named.st_mode & 0o022
                ):
                    raise LedgerError("lifecycle journal identity changed")
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
                raise LedgerError("lifecycle journal cannot be trusted") from error
            finally:
                os.close(descriptor)
            if not isinstance(value, dict) or canonical_line(value) != payload:
                raise LedgerError("lifecycle journal cannot be trusted")
            _validate_journal_shape(value)
            if value["phase"] != "committed":
                if pending is not None:
                    raise LedgerError("multiple nonterminal lifecycle journals exist")
                pending = value
        return pending
    finally:
        os.close(directory)


def _validate_journal_shape(value: dict[str, JSONValue]) -> None:
    """Require the stable journal envelope without interpreting gate-specific payloads."""
    transition = value.get("transition")
    if not isinstance(transition, str) or transition not in JOURNAL_PHASES:
        raise LedgerError("lifecycle journal transition is invalid")
    required = {"schema_version", "transition", "transition_id", "phase", "inputs"}
    optional = (
        {"prior_pointer_sha256"}
        if transition == "begin"
        else {"pointer_sha256", "attempt_inventory", "external_exports"}
    )
    if transition == "seal":
        optional.update({"operations_sha256", "prerequisite_attestation"})
    transition_id = value.get("transition_id")
    phase = value.get("phase")
    if (
        set(value) != required | optional
        or value.get("schema_version") != 1
        or not isinstance(transition_id, str)
        or SHA64.fullmatch(transition_id) is None
        or not isinstance(phase, str)
        or phase not in JOURNAL_PHASES[transition]
        or not isinstance(value.get("inputs"), dict)
    ):
        raise LedgerError("lifecycle journal schema is invalid")
    if transition == "seal":
        _validate_seal_prerequisite_attestation(value)


def _validate_seal_prerequisite_attestation(value: dict[str, JSONValue]) -> None:
    """Validate the bounded seal-attestation envelope without interpreting receipts."""
    attestation = value.get("prerequisite_attestation")
    inputs = value.get("inputs")
    if not isinstance(attestation, dict) or not isinstance(inputs, dict):
        raise LedgerError("seal prerequisite attestation is invalid")
    required = {
        "schema_version",
        "attempt_id",
        "head",
        "base_sha",
        "receipts",
        "attempt_inventory",
        "external_exports",
    }
    attempt_id = attestation.get("attempt_id")
    head = attestation.get("head")
    base_sha = attestation.get("base_sha")
    if (
        set(attestation) != required
        or attestation.get("schema_version") != 1
        or not isinstance(attempt_id, str)
        or SHA64.fullmatch(attempt_id) is None
        or not isinstance(head, str)
        or SHA40.fullmatch(head) is None
        or not isinstance(base_sha, str)
        or SHA40.fullmatch(base_sha) is None
        or not isinstance(attestation.get("receipts"), list)
        or not _valid_inventory(attestation.get("attempt_inventory"))
        or not isinstance(attestation.get("external_exports"), list)
    ):
        raise LedgerError("seal prerequisite attestation is invalid")
    digest = hashlib.sha256(canonical_line(attestation)).hexdigest()
    if inputs.get("prerequisite_attestation_sha256") != digest:
        raise LedgerError("seal prerequisite attestation hash is invalid")


def _validate_private_lifecycle_transition(
    pending_journal: dict[str, JSONValue] | None,
    *,
    action_class: str,
    arguments: Mapping[str, JSONValue],
    status: str,
) -> None:
    """Allow only the ledger event exactly bound to a prepared internal journal."""
    if pending_journal is None:
        raise LedgerError("private lifecycle transition has no pending journal")
    if pending_journal["phase"] != "prepared" or status != "passed":
        raise LedgerError("private lifecycle transition is not resumable")
    expected_action, expected_arguments = _expected_journal_event(pending_journal)
    if action_class != expected_action or dict(arguments) != expected_arguments:
        raise LedgerError(
            "private lifecycle transition is not the exact pending lifecycle transition"
        )


def _expected_journal_event(
    journal: dict[str, JSONValue],
) -> tuple[str, dict[str, JSONValue]]:
    """Return the one lifecycle ledger event a prepared journal is allowed to resume."""
    transition = journal["transition"]
    transition_id = journal["transition_id"]
    inputs = journal["inputs"]
    if (
        not isinstance(transition, str)
        or not isinstance(transition_id, str)
        or not isinstance(inputs, dict)
    ):
        raise LedgerError("lifecycle journal schema is invalid")
    if transition == "begin":
        expected_keys = {"head", "plan_sha256", "review_round", "operations_terminal_hash"}
        head = inputs.get("head")
        if (
            set(inputs) != expected_keys
            or not isinstance(head, str)
            or SHA40.fullmatch(head) is None
            or hashlib.sha256(canonical_line(inputs)).hexdigest() != transition_id
        ):
            raise LedgerError("begin lifecycle journal is invalid")
        return "final_attempt_started", {"attempt_id": transition_id, "head": head}
    if transition not in {"abandon", "reopen"}:
        raise LedgerError("lifecycle journal does not append an operation")
    expected_keys = {
        "attempt_id",
        "plan_sha256",
        "review_round",
        "failure_receipt_sha256",
        "failure_receipt_path",
        "operations_terminal_hash",
        "operations_seal_sha256",
    }
    attempt_id = inputs.get("attempt_id")
    failure_sha = inputs.get("failure_receipt_sha256")
    failure_path = inputs.get("failure_receipt_path")
    if (
        set(inputs) != expected_keys
        or not isinstance(attempt_id, str)
        or SHA64.fullmatch(attempt_id) is None
        or not isinstance(failure_sha, str)
        or SHA64.fullmatch(failure_sha) is None
        or not isinstance(failure_path, str)
        or not failure_path
        or hashlib.sha256(canonical_line(inputs)).hexdigest() != transition_id
    ):
        raise LedgerError("terminal lifecycle journal is invalid")
    event_key = "abandon_id" if transition == "abandon" else "reopen_id"
    action = "final_attempt_abandoned" if transition == "abandon" else "seal_reopened"
    return action, {
        event_key: transition_id,
        "attempt_id": attempt_id,
        "failure_path": failure_path,
        "failure_sha256": failure_sha,
    }


def _revalidate_evidence_root(evidence_lock: EvidenceWriterLock) -> None:
    rebound, _ = open_parent(evidence_lock.evidence_root / ".identity-anchor", create=False)
    try:
        current = os.fstat(evidence_lock.parent_descriptor)
        named = os.fstat(rebound)
        if (current.st_dev, current.st_ino) != (named.st_dev, named.st_ino):
            raise LedgerError("locked evidence root pathname identity changed")
    finally:
        os.close(rebound)


def _validate_writer_lock(evidence_lock: EvidenceWriterLock) -> None:
    """Require the held lock descriptor to remain the named private lock file."""
    metadata = os.fstat(evidence_lock.descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o022
        or (metadata.st_dev, metadata.st_ino) != evidence_lock.file_identity
    ):
        raise LedgerError("evidence writer lock identity is unsafe")
    try:
        named = os.stat(
            ".evidence-writer.lock",
            dir_fd=evidence_lock.parent_descriptor,
            follow_symlinks=False,
        )
    except OSError as error:
        raise LedgerError("evidence writer lock identity changed") from error
    if (
        not stat.S_ISREG(named.st_mode)
        or named.st_nlink != 1
        or named.st_uid != os.geteuid()
        or named.st_mode & 0o022
        or (named.st_dev, named.st_ino) != evidence_lock.file_identity
    ):
        raise LedgerError("evidence writer lock identity changed")
