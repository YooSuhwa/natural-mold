#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///

"""Create and finalize one identity-bound isolated-run manifest."""

from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path

type Identity = tuple[int, int]


def _identity(metadata: os.stat_result) -> Identity:
    return metadata.st_dev, metadata.st_ino


def _open_parent(path: Path) -> int:
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise OSError("manifest path must be absolute")
    current_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for component in path.parent.parts[1:]:
            child_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=current_fd,
            )
            os.close(current_fd)
            current_fd = child_fd
        metadata = os.fstat(current_fd)
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
            raise OSError("manifest parent is not private")
        return current_fd
    except OSError:
        os.close(current_fd)
        raise


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written == 0:
            raise OSError("manifest write made no progress")
        view = view[written:]


def prepare_manifest(path: Path) -> tuple[Identity, Identity]:
    """Create one private regular file and return parent/file identities."""
    parent_fd = _open_parent(path)
    try:
        file_fd = os.open(
            path.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent_fd,
        )
        try:
            metadata = os.fstat(file_fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise OSError("manifest is not a private regular file")
            os.fsync(file_fd)
            os.fsync(parent_fd)
            return _identity(os.fstat(parent_fd)), _identity(metadata)
        finally:
            os.close(file_fd)
    finally:
        os.close(parent_fd)


def finalize_manifest(
    path: Path, parent_identity: Identity, file_identity: Identity, payload: bytes
) -> None:
    """Replace bytes only through the still-bound parent and regular file identity."""
    parent_fd = _open_parent(path)
    try:
        if _identity(os.fstat(parent_fd)) != parent_identity:
            raise OSError("manifest parent identity changed")
        named = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(named.st_mode) or named.st_nlink != 1:
            raise OSError("manifest identity changed")
        file_fd = os.open(
            path.name,
            os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
        try:
            opened = os.fstat(file_fd)
            if _identity(named) != file_identity or _identity(opened) != file_identity:
                raise OSError("manifest identity changed")
            os.ftruncate(file_fd, 0)
            _write_all(file_fd, payload)
            os.fsync(file_fd)
            os.fsync(parent_fd)
        finally:
            os.close(file_fd)
    finally:
        os.close(parent_fd)


def _parse_identity(value: str) -> Identity:
    device, inode = value.split(":", 1)
    return int(device), int(inode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("path")
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("path")
    finalize.add_argument("parent_identity")
    finalize.add_argument("file_identity")
    finalize.add_argument("payload")
    arguments = parser.parse_args()
    try:
        if arguments.command == "prepare":
            parent, file = prepare_manifest(Path(arguments.path))
            print(f"{parent[0]}:{parent[1]} {file[0]}:{file[1]}")
        else:
            finalize_manifest(
                Path(arguments.path),
                _parse_identity(arguments.parent_identity),
                _parse_identity(arguments.file_identity),
                arguments.payload.encode(),
            )
    except (OSError, ValueError):
        print("manifest_failed")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
