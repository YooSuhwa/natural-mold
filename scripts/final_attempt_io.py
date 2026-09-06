"""Descriptor-bound I/O primitives for final-attempt lifecycle state."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from operation_ledger_chain import _verify_bytes
from operation_ledger_format import JSONValue, LedgerError, canonical_line
from operation_ledger_fs import (
    close,
    open_existing,
    open_existing_at,
    open_parent,
    read_all,
)
from operation_ledger_writer import (
    EvidenceWriterLock,
    _revalidate_evidence_root,
    current_evidence_lock,
)

type CrashHook = Callable[[str], None]

SHA40: Final = re.compile(r"[0-9a-f]{40}\Z")
SHA64: Final = re.compile(r"[0-9a-f]{64}\Z")


class UnsafeFinalAttemptEntry(RuntimeError):
    """A lifecycle control entry failed its filesystem trust contract."""


def validate_trusted_regular(metadata: os.stat_result, *, label: str) -> None:
    """Require an owned, single-link, non-writable regular control file."""
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o022
    ):
        raise UnsafeFinalAttemptEntry(f"{label} is unsafe")


def _validate_trusted_directory(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o022
    ):
        raise LifecycleError("inventory contains an unsafe directory")


class LifecycleError(RuntimeError):
    """The final-attempt state machine failed closed."""


def _canonical(value: Mapping[str, JSONValue]) -> bytes:
    return canonical_line(value)


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_json(value: Mapping[str, JSONValue]) -> str:
    return _digest_bytes(_canonical(value))


def _read_regular(path: Path, *, maximum: int = 16 * 1024 * 1024) -> bytes:
    absolute = path.absolute()
    try:
        active_lock = current_evidence_lock()
        if active_lock is not None and absolute.is_relative_to(active_lock.evidence_root):
            parent_descriptor, name = _locked_parent(active_lock, absolute, create=False)
        else:
            parent_descriptor, name = open_parent(absolute, create=False)
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_descriptor,
        )
    except (OSError, LedgerError) as error:
        raise LifecycleError(f"cannot open trusted file: {path}") from error
    try:
        metadata = os.fstat(descriptor)
        try:
            validate_trusted_regular(metadata, label="lifecycle file")
        except UnsafeFinalAttemptEntry as error:
            raise LifecycleError(f"file identity is unsafe: {path}") from error
        chunks: list[bytes] = []
        size = 0
        while chunk := os.read(descriptor, min(1024 * 1024, maximum + 1 - size)):
            size += len(chunk)
            if size > maximum:
                raise LifecycleError(f"trusted file is too large: {path}")
            chunks.append(chunk)
        named = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        try:
            validate_trusted_regular(named, label="lifecycle file")
        except UnsafeFinalAttemptEntry as error:
            raise LifecycleError(f"file identity is unsafe: {path}") from error
        if (named.st_dev, named.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise LifecycleError(f"file identity changed while reading: {path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)
        os.close(parent_descriptor)


def _read_json(path: Path) -> dict[str, JSONValue]:
    payload = _read_regular(path)
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise LifecycleError(f"cannot read lifecycle JSON: {path}") from error
    if not isinstance(value, dict) or _canonical(value) != payload:
        raise LifecycleError(f"lifecycle JSON is not canonical: {path}")
    return value


def _read_optional(path: Path) -> dict[str, JSONValue] | None:
    try:
        return _read_json(path)
    except LifecycleError as error:
        if isinstance(error.__cause__, FileNotFoundError):
            return None
        raise


def _hash_optional(path: Path) -> str | None:
    try:
        return _digest_bytes(_read_regular(path))
    except LifecycleError as error:
        if isinstance(error.__cause__, FileNotFoundError):
            return None
        raise


def _revalidate_parent(path: Path, descriptor: int, expected: os.stat_result) -> None:
    try:
        rebound, _ = open_parent(path.absolute(), create=False)
    except LedgerError as error:
        raise LifecycleError(f"lifecycle parent path changed: {path}") from error
    try:
        current = os.fstat(descriptor)
        named = os.fstat(rebound)
        expected_identity = (expected.st_dev, expected.st_ino)
        if (current.st_dev, current.st_ino) != expected_identity or (
            named.st_dev,
            named.st_ino,
        ) != expected_identity:
            raise LifecycleError(f"lifecycle parent identity changed: {path}")
    finally:
        os.close(rebound)


def _validate_named_write_inode(
    parent_descriptor: int,
    name: str,
    descriptor: int,
    parent_identity: os.stat_result,
    path: Path,
) -> None:
    named = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    opened = os.fstat(descriptor)
    try:
        validate_trusted_regular(named, label="lifecycle write target")
        validate_trusted_regular(opened, label="lifecycle write inode")
    except UnsafeFinalAttemptEntry as error:
        raise LifecycleError(f"lifecycle write identity changed: {path}") from error
    current_parent = os.fstat(parent_descriptor)
    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino) or (
        current_parent.st_dev,
        current_parent.st_ino,
    ) != (parent_identity.st_dev, parent_identity.st_ino):
        raise LifecycleError(f"lifecycle write identity changed: {path}")


def _unlink_owned_temporary(
    parent_descriptor: int,
    temporary: str,
    descriptor: int,
) -> bool:
    """Remove a failed pre-rename temporary only while its name still owns our inode."""
    try:
        named = os.stat(temporary, dir_fd=parent_descriptor, follow_symlinks=False)
        opened = os.fstat(descriptor)
        if (named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino):
            os.unlink(temporary, dir_fd=parent_descriptor)
            return True
    except FileNotFoundError:
        pass
    return False


def _validate_named_directory_inode(
    parent_descriptor: int,
    name: str,
    descriptor: int,
    parent_identity: os.stat_result,
    path: Path,
) -> None:
    opened = os.fstat(descriptor)
    named = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    current_parent = os.fstat(parent_descriptor)
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(named.st_mode)
        or opened.st_uid != os.geteuid()
        or named.st_uid != os.geteuid()
        or opened.st_mode & 0o022
        or named.st_mode & 0o022
        or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        or (current_parent.st_dev, current_parent.st_ino)
        != (parent_identity.st_dev, parent_identity.st_ino)
    ):
        raise LifecycleError(f"created directory identity changed: {path}")
    _revalidate_parent(path, parent_descriptor, parent_identity)


def _atomic_json(
    path: Path,
    value: Mapping[str, JSONValue],
    hook: CrashHook | None,
    label: str,
    evidence_lock: EvidenceWriterLock,
) -> None:
    absolute = path.absolute()
    try:
        parent_descriptor, name = _locked_parent(evidence_lock, absolute, create=True)
    except LedgerError as error:
        raise LifecycleError(f"unsafe lifecycle write parent: {path}") from error
    parent_identity = os.fstat(parent_descriptor)
    try:
        existing = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        try:
            validate_trusted_regular(existing, label="lifecycle write target")
        except UnsafeFinalAttemptEntry as error:
            os.close(parent_descriptor)
            raise LifecycleError(f"unsafe lifecycle write target: {path}") from error
    temporary = f".{name}.tmp-{uuid.uuid4().hex}"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=parent_descriptor,
    )
    renamed = False
    try:
        _validate_named_write_inode(
            parent_descriptor, temporary, descriptor, parent_identity, absolute
        )
        payload = _canonical(value)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise LifecycleError("atomic lifecycle write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        if hook is not None:
            hook(f"{label}:file_fsync")
        _validate_named_write_inode(
            parent_descriptor, temporary, descriptor, parent_identity, absolute
        )
        os.replace(temporary, name, src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor)
        renamed = True
        if hook is not None:
            hook(f"{label}:replace")
        _validate_named_write_inode(parent_descriptor, name, descriptor, parent_identity, absolute)
        _revalidate_parent(absolute, parent_descriptor, parent_identity)
        os.fsync(parent_descriptor)
        if hook is not None:
            hook(f"{label}:parent_fsync")
        _validate_named_write_inode(parent_descriptor, name, descriptor, parent_identity, absolute)
        _revalidate_parent(absolute, parent_descriptor, parent_identity)
    except (OSError, RuntimeError):
        if not renamed:
            with suppress(OSError):
                _unlink_owned_temporary(parent_descriptor, temporary, descriptor)
        raise
    finally:
        os.close(descriptor)
        os.close(parent_descriptor)


def _create_directory(
    path: Path, hook: CrashHook | None, label: str, evidence_lock: EvidenceWriterLock
) -> None:
    try:
        absolute = path.absolute()
        parent_descriptor, name = _locked_parent(evidence_lock, absolute, create=False)
        parent_identity = os.fstat(parent_descriptor)
        os.mkdir(name, 0o700, dir_fd=parent_descriptor)
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_descriptor,
        )
    except (OSError, LedgerError) as error:
        raise LifecycleError(f"cannot create trusted directory: {path}") from error
    try:
        _validate_named_directory_inode(
            parent_descriptor, name, descriptor, parent_identity, absolute
        )
        os.fsync(descriptor)
        if hook is not None:
            hook(f"{label}:directory_fsync")
        _validate_named_directory_inode(
            parent_descriptor, name, descriptor, parent_identity, absolute
        )
        os.fsync(parent_descriptor)
        if hook is not None:
            hook(f"{label}:parent_fsync")
        _validate_named_directory_inode(
            parent_descriptor, name, descriptor, parent_identity, absolute
        )
    finally:
        os.close(descriptor)
        os.close(parent_descriptor)


def _ensure_directory(path: Path, evidence_lock: EvidenceWriterLock) -> None:
    try:
        absolute = path.absolute()
        parent_descriptor, name = _locked_parent(evidence_lock, absolute, create=False)
        parent_identity = os.fstat(parent_descriptor)
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_descriptor,
            )
        except FileNotFoundError:
            os.mkdir(name, 0o700, dir_fd=parent_descriptor)
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_descriptor,
            )
            os.fsync(parent_descriptor)
        metadata = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            (metadata.st_dev, metadata.st_ino) != (named.st_dev, named.st_ino)
            or not stat.S_ISDIR(named.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o022
        ):
            raise LifecycleError(f"lifecycle directory is not private: {path}")
        _revalidate_parent(absolute, parent_descriptor, parent_identity)
    except (OSError, LedgerError) as error:
        raise LifecycleError(f"lifecycle directory cannot be trusted: {path}") from error
    finally:
        with suppress(UnboundLocalError):
            os.close(descriptor)
        with suppress(UnboundLocalError):
            os.close(parent_descriptor)


def _locked_parent(
    evidence_lock: EvidenceWriterLock, path: Path, *, create: bool
) -> tuple[int, str]:
    _revalidate_evidence_root(evidence_lock)
    try:
        relative = path.relative_to(evidence_lock.evidence_root)
    except ValueError as error:
        raise LifecycleError("lifecycle mutation escapes the locked evidence root") from error
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise LifecycleError("lifecycle mutation path is invalid")
    descriptor = os.dup(evidence_lock.parent_descriptor)
    try:
        for component in relative.parts[:-1]:
            try:
                child = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, 0o700, dir_fd=descriptor)
                child = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=descriptor,
                )
            metadata = os.fstat(child)
            if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
                os.close(child)
                raise LifecycleError("lifecycle descendant directory is not private")
            os.close(descriptor)
            descriptor = child
        _revalidate_evidence_root(evidence_lock)
        return descriptor, relative.parts[-1]
    except (OSError, LifecycleError):
        os.close(descriptor)
        raise


def _entry_metadata(path: Path) -> os.stat_result | None:
    absolute = path.absolute()
    active_lock = current_evidence_lock()
    try:
        if active_lock is not None and absolute.is_relative_to(active_lock.evidence_root):
            parent_descriptor, name = _locked_parent(active_lock, absolute, create=False)
        else:
            parent_descriptor, name = open_parent(absolute, create=False)
        try:
            return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        finally:
            os.close(parent_descriptor)
    except FileNotFoundError:
        return None


def _tip(path: Path) -> str:
    entries = _verified_entries(path)
    value = entries[-1].get("entry_hash")
    if not isinstance(value, str):
        raise LifecycleError("operations ledger tip is invalid")
    return value


def _events(
    path: Path, action: str, arguments: Mapping[str, JSONValue]
) -> list[dict[str, JSONValue]]:
    return [
        entry
        for entry in _verified_entries(path)
        if entry.get("action_class") == action
        and entry.get("task_id") == "final-attempt"
        and entry.get("status") == "passed"
        and entry.get("arguments") == dict(arguments)
    ]


def _verified_entries(path: Path) -> list[dict[str, JSONValue]]:
    active_lock = current_evidence_lock()
    bound = (
        open_existing_at(active_lock.parent_descriptor, active_lock.evidence_root, path)
        if active_lock is not None and path.absolute().parent == active_lock.evidence_root
        else open_existing(path.absolute())
    )
    try:
        return _verify_bytes(read_all(bound))
    finally:
        close(bound)


def load_bound_operations(
    operations: Path, evidence_lock: EvidenceWriterLock
) -> list[dict[str, JSONValue]]:
    """Read the operations chain only through the active evidence-root binding."""
    if current_evidence_lock() is not evidence_lock:
        raise LifecycleError("operations read requires the active evidence-writer lock")
    return _verified_entries(operations)


@dataclass(frozen=True, slots=True)
class FinalGateOutputBinding:
    """An open attempt-directory descriptor retained through one F1/F4 write."""

    descriptor: int
    attempt_dir: Path
    identity: os.stat_result
    evidence_lock: EvidenceWriterLock


@contextmanager
def bind_final_gate_output(
    evidence_lock: EvidenceWriterLock, attempt_dir: Path
) -> Iterator[FinalGateOutputBinding]:
    """Bind the current attempt directory without following a replaceable pathname."""
    if current_evidence_lock() is not evidence_lock:
        raise LifecycleError("final gate output binding requires the active evidence lock")
    descriptor, _ = _locked_parent(
        evidence_lock,
        attempt_dir.absolute() / ".final-gate-output-anchor",
        create=False,
    )
    identity = os.fstat(descriptor)
    _validate_trusted_directory(identity)
    _revalidate_parent(attempt_dir / ".final-gate-output-anchor", descriptor, identity)
    try:
        yield FinalGateOutputBinding(descriptor, attempt_dir.absolute(), identity, evidence_lock)
    finally:
        os.close(descriptor)


def write_bound_final_gate_output(
    binding: FinalGateOutputBinding,
    output: Path,
    payload: Mapping[str, JSONValue],
) -> None:
    """Atomically create an F1/F4 result through the retained attempt dir FD."""
    if (
        current_evidence_lock() is not binding.evidence_lock
        or output.absolute().parent != binding.attempt_dir
        or output.name not in {"f1-history.json", "f4-scope.json"}
    ):
        raise LifecycleError("final gate output path is outside the bound attempt")
    _revalidate_parent(output, binding.descriptor, binding.identity)
    try:
        os.stat(output.name, dir_fd=binding.descriptor, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise LifecycleError("final gate output already exists")
    temporary = f".{output.name}.tmp-{uuid.uuid4().hex}"
    installed = False
    completed = False
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=binding.descriptor,
        )
        _validate_named_write_inode(
            binding.descriptor, temporary, descriptor, binding.identity, output
        )
        content = canonical_line(dict(payload))
        written = 0
        while written < len(content):
            count = os.write(descriptor, content[written:])
            if count <= 0:
                raise LifecycleError("final gate output write made no progress")
            written += count
        os.fsync(descriptor)
        _validate_named_write_inode(
            binding.descriptor, temporary, descriptor, binding.identity, output
        )
        _revalidate_parent(output, binding.descriptor, binding.identity)
        os.replace(
            temporary,
            output.name,
            src_dir_fd=binding.descriptor,
            dst_dir_fd=binding.descriptor,
        )
        installed = True
        _validate_named_write_inode(
            binding.descriptor, output.name, descriptor, binding.identity, output
        )
        _revalidate_parent(output, binding.descriptor, binding.identity)
        os.fsync(binding.descriptor)
        _validate_named_write_inode(
            binding.descriptor, output.name, descriptor, binding.identity, output
        )
        _revalidate_parent(output, binding.descriptor, binding.identity)
        completed = True
    except (OSError, LedgerError) as error:
        raise LifecycleError("final gate output write failed closed") from error
    finally:
        if descriptor >= 0:
            if not completed:
                visible_name = output.name if installed else temporary
                with suppress(OSError):
                    if _unlink_owned_temporary(binding.descriptor, visible_name, descriptor):
                        os.fsync(binding.descriptor)
            os.close(descriptor)
