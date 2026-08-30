"""Identity-bound filesystem operations for durable ledger writes."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from operation_ledger_format import LedgerError

type Identity = tuple[int, int]


@dataclass(frozen=True, slots=True)
class BoundLedger:
    parent_fd: int
    file_fd: int
    name: str
    parent_identity: Identity
    file_identity: Identity


def _identity(metadata: os.stat_result) -> Identity:
    return metadata.st_dev, metadata.st_ino


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _trusted_directory(descriptor: int, *, final: bool) -> os.stat_result:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid not in {0, os.geteuid()}:
        raise LedgerError("ledger parent is not trusted")
    if final and (metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022):
        raise LedgerError("ledger parent is not private")
    return metadata


def open_parent(path: Path, *, create: bool) -> tuple[int, str]:
    """Open an absolute parent component-by-component without following links."""
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise LedgerError("ledger path must be absolute")
    current_fd = os.open("/", _directory_flags())
    try:
        parts = path.parent.parts[1:]
        for index, component in enumerate(parts):
            try:
                child_fd = os.open(component, _directory_flags(), dir_fd=current_fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, 0o700, dir_fd=current_fd)
                child_fd = os.open(component, _directory_flags(), dir_fd=current_fd)
            _trusted_directory(child_fd, final=index == len(parts) - 1)
            os.close(current_fd)
            current_fd = child_fd
        _trusted_directory(current_fd, final=True)
        return current_fd, path.name
    except (OSError, LedgerError) as error:
        os.close(current_fd)
        if isinstance(error, LedgerError):
            raise
        raise LedgerError("ledger parent cannot be opened safely") from error


def _validate_file(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
    ):
        raise LedgerError("ledger file identity is unsafe")


def _bound(parent_fd: int, file_fd: int, name: str) -> BoundLedger:
    parent = os.fstat(parent_fd)
    opened = os.fstat(file_fd)
    _validate_file(opened)
    named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if _identity(named) != _identity(opened):
        raise LedgerError("ledger pathname identity changed")
    return BoundLedger(
        parent_fd=parent_fd,
        file_fd=file_fd,
        name=name,
        parent_identity=_identity(parent),
        file_identity=_identity(opened),
    )


def open_existing(path: Path) -> BoundLedger:
    parent_fd, name = open_parent(path, create=False)
    try:
        file_fd = os.open(
            name,
            os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
        try:
            return _bound(parent_fd, file_fd, name)
        except (OSError, LedgerError):
            os.close(file_fd)
            raise
    except (OSError, LedgerError) as error:
        os.close(parent_fd)
        if isinstance(error, LedgerError):
            raise
        raise LedgerError("ledger file cannot be opened safely") from error


def create_exclusive(path: Path) -> BoundLedger:
    parent_fd, name = open_parent(path, create=True)
    try:
        file_fd = os.open(
            name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent_fd,
        )
        try:
            return _bound(parent_fd, file_fd, name)
        except (OSError, LedgerError):
            os.close(file_fd)
            raise
    except FileExistsError as error:
        os.close(parent_fd)
        raise LedgerError("operations ledger already exists") from error
    except (OSError, LedgerError) as error:
        os.close(parent_fd)
        if isinstance(error, LedgerError):
            raise
        raise LedgerError("operations ledger cannot be created safely") from error


def validate_name(bound: BoundLedger) -> None:
    if _identity(os.fstat(bound.parent_fd)) != bound.parent_identity:
        raise LedgerError("ledger parent identity changed")
    named = os.stat(bound.name, dir_fd=bound.parent_fd, follow_symlinks=False)
    opened = os.fstat(bound.file_fd)
    _validate_file(named)
    if _identity(named) != bound.file_identity or _identity(opened) != bound.file_identity:
        raise LedgerError("ledger pathname identity changed")


def read_all(bound: BoundLedger) -> bytes:
    os.lseek(bound.file_fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while chunk := os.read(bound.file_fd, 1024 * 1024):
        chunks.append(chunk)
    return b"".join(chunks)


def write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written == 0:
            raise LedgerError("ledger write made no progress")
        view = view[written:]


def rollback(bound: BoundLedger, verified_eof: int) -> None:
    os.ftruncate(bound.file_fd, verified_eof)
    os.fsync(bound.file_fd)
    os.fsync(bound.parent_fd)


def remove_created(bound: BoundLedger) -> None:
    validate_name(bound)
    os.unlink(bound.name, dir_fd=bound.parent_fd)
    os.fsync(bound.parent_fd)


def close(bound: BoundLedger) -> None:
    os.close(bound.file_fd)
    os.close(bound.parent_fd)
