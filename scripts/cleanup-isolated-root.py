#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///

# ─── How to run ───
# 1. Run through the hermetic command wrapper; this is not a public CLI.
# 2. Capture identity: python3 scripts/cleanup-isolated-root.py identity <root>
# 3. Clean by identity: python3 scripts/cleanup-isolated-root.py cleanup <root> <dev> <ino>
# ──────────────────

"""Remove one isolated run root only when its filesystem identity still matches."""

from __future__ import annotations

import argparse
import os
import secrets
import stat
from pathlib import Path

RUN_ROOT_PREFIX = ".moldy-test-run."
QUARANTINE_PREFIX = ".moldy-test-quarantine."


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _open_directory(name: str | Path, *, dir_fd: int | None = None) -> int:
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)


def read_identity(path: Path) -> tuple[int, int]:
    """Return the no-follow device and inode for one owned directory."""
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode):
        raise NotADirectoryError
    return _identity(metadata)


def _remove_contents(directory_fd: int) -> None:
    for name in os.listdir(directory_fd):
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode):
            os.unlink(name, dir_fd=directory_fd)
            continue
        child_fd = _open_directory(name, dir_fd=directory_fd)
        try:
            if _identity(os.fstat(child_fd)) != _identity(metadata):
                raise OSError("directory identity changed")
            _remove_contents(child_fd)
            if _identity(os.fstat(child_fd)) != _identity(metadata):
                raise OSError("directory identity changed")
        finally:
            os.close(child_fd)
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if _identity(current) != _identity(metadata):
            raise OSError("directory identity changed")
        os.rmdir(name, dir_fd=directory_fd)


def cleanup_owned_root(path: Path, expected: tuple[int, int]) -> str:
    """Quarantine and remove the exact owned directory, preserving mismatches."""
    if not path.is_absolute() or not path.name.startswith(RUN_ROOT_PREFIX):
        return "identity_mismatch"
    parent_fd = _open_directory(path.parent)
    quarantine = f"{QUARANTINE_PREFIX}{secrets.token_hex(16)}"
    try:
        try:
            current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return "identity_mismatch"
        if not stat.S_ISDIR(current.st_mode) or _identity(current) != expected:
            return "identity_mismatch"
        os.rename(path.name, quarantine, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        quarantined = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
        if _identity(quarantined) != expected:
            try:
                os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                os.rename(quarantine, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            return "identity_mismatch"
        root_fd = _open_directory(quarantine, dir_fd=parent_fd)
        try:
            if _identity(os.fstat(root_fd)) != expected:
                return "identity_mismatch"
            _remove_contents(root_fd)
            if _identity(os.fstat(root_fd)) != expected:
                return "identity_mismatch"
        finally:
            os.close(root_fd)
        final = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
        if _identity(final) != expected:
            return "identity_mismatch"
        os.rmdir(quarantine, dir_fd=parent_fd)
        try:
            os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return "removed"
        return "root_recreated"
    finally:
        os.close(parent_fd)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    identity = subparsers.add_parser("identity")
    identity.add_argument("path")
    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("path")
    cleanup.add_argument("device", type=int)
    cleanup.add_argument("inode", type=int)
    return parser


def main() -> int:
    """Run the internal boundary without exposing paths or identities on errors."""
    arguments = _parser().parse_args()
    try:
        if arguments.command == "identity":
            device, inode = read_identity(Path(arguments.path))
            print(f"{device}:{inode}")
            return 0
        result = cleanup_owned_root(
            Path(arguments.path),
            (arguments.device, arguments.inode),
        )
    except OSError:
        print("cleanup_failed")
        return 3
    print(result)
    return 0 if result == "removed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
