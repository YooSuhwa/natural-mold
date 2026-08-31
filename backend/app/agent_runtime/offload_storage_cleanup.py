"""Conversation-serialized, dirfd-confined internal offload cleanup.

All application mutation APIs share the persistent lock below. The 0700 internal
roots exclude untrusted OS principals; portable POSIX cannot conditionally unlink
a pathname against an inode when a non-cooperating same-UID process races it.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from app.agent_runtime.offload_storage_fd import (
    directory_flags,
    nofollow_flags,
    open_scoped_directory,
    raise_directory_error,
)
from app.agent_runtime.offload_storage_lock import (
    conversation_deletion_lock,  # noqa: F401 -- compatibility export
    conversation_mutation_lock,
)
from app.agent_runtime.offload_storage_types import OffloadMutationScope, OffloadSecurityError


@dataclass(frozen=True, slots=True)
class _DirectoryIdentity:
    device: int
    inode: int


def _identity(metadata: os.stat_result) -> _DirectoryIdentity:
    return _DirectoryIdentity(device=metadata.st_dev, inode=metadata.st_ino)


def _require_cleanup_scope(relative: tuple[str, ...]) -> None:
    if len(relative) < 3 or relative[0] not in {"history", "spill"}:
        raise OffloadSecurityError(reason="offload cleanup scope is invalid")


def _mutation_scope(anchor: Path, relative: tuple[str, ...]) -> OffloadMutationScope:
    _require_cleanup_scope(relative)
    return OffloadMutationScope(
        internal_root=anchor,
        owner=relative[1],
        conversation=relative[2],
    )


@contextmanager
def conversation_cleanup_lock(anchor: Path, relative: tuple[str, ...]) -> Iterator[None]:
    """Compatibility wrapper for one cleanup target's conversation lock."""
    with conversation_mutation_lock(_mutation_scope(anchor, relative)):
        yield


def preflight_scoped_tree(anchor: Path, relative: tuple[str, ...]) -> _DirectoryIdentity | None:
    """Capture a cleanup target identity before waiting for its mutation lock."""
    _require_cleanup_scope(relative)
    parent = open_scoped_directory(anchor, relative[:-1])
    if parent is None:
        return None
    try:
        try:
            metadata = os.stat(relative[-1], dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISDIR(metadata.st_mode):
            raise OffloadSecurityError(reason="offload cleanup target is not a directory")
        return _identity(metadata)
    finally:
        os.close(parent)


def _current_identity(parent: int, name: str) -> _DirectoryIdentity:
    try:
        return _identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
    except FileNotFoundError as error:
        raise OffloadSecurityError(reason="offload cleanup target changed") from error


def _open_matching_directory(parent: int, name: str, expected: _DirectoryIdentity) -> int:
    try:
        descriptor = os.open(name, directory_flags(), dir_fd=parent)
    except OSError as error:
        raise_directory_error(error)
    opened = os.fstat(descriptor)
    if not stat.S_ISDIR(opened.st_mode) or _identity(opened) != expected:
        os.close(descriptor)
        raise OffloadSecurityError(reason="offload cleanup target changed")
    if _current_identity(parent, name) != expected:
        os.close(descriptor)
        raise OffloadSecurityError(reason="offload cleanup target changed")
    return descriptor


def _unlink_regular(parent: int, name: str, expected: _DirectoryIdentity) -> None:
    flags = nofollow_flags(os.O_RDONLY)
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
    except OSError as error:
        raise_directory_error(error)
    try:
        opened = os.fstat(descriptor)
        valid = (
            stat.S_ISREG(opened.st_mode)
            and opened.st_nlink == 1
            and _identity(opened) == expected
            and _current_identity(parent, name) == expected
        )
        if not valid:
            raise OffloadSecurityError(reason="offload cleanup entry changed")
        try:
            os.unlink(name, dir_fd=parent)
        except FileNotFoundError as error:
            raise OffloadSecurityError(reason="offload cleanup entry changed") from error
    finally:
        os.close(descriptor)


def _remove_directory_contents(descriptor: int) -> int:
    removed_files = 0
    for name in sorted(os.listdir(descriptor)):  # noqa: PTH208 -- descriptor pins scope
        try:
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError as error:
            raise OffloadSecurityError(reason="offload cleanup entry changed") from error
        entry = _identity(metadata)
        if stat.S_ISDIR(metadata.st_mode):
            child = _open_matching_directory(descriptor, name, entry)
            try:
                removed_files += _remove_directory_contents(child)
                if _current_identity(descriptor, name) != entry:
                    raise OffloadSecurityError(reason="offload cleanup entry changed")
                try:
                    os.rmdir(name, dir_fd=descriptor)
                except FileNotFoundError as error:
                    raise OffloadSecurityError(reason="offload cleanup entry changed") from error
            finally:
                os.close(child)
        elif stat.S_ISREG(metadata.st_mode):
            _unlink_regular(descriptor, name, entry)
            removed_files += 1
        else:
            raise OffloadSecurityError(reason="offload cleanup entry is unsupported")
    return removed_files


def remove_scoped_tree_with_status_locked(
    anchor: Path, relative: tuple[str, ...]
) -> tuple[bool, int]:
    """Remove a tree while the caller owns its conversation mutation lock."""
    return remove_preflighted_scoped_tree_with_status_locked(
        anchor,
        relative,
        preflight_scoped_tree(anchor, relative),
    )


def remove_preflighted_scoped_tree_with_status_locked(
    anchor: Path,
    relative: tuple[str, ...],
    expected: _DirectoryIdentity | None,
) -> tuple[bool, int]:
    """Remove only the target identity observed before lock acquisition."""
    _require_cleanup_scope(relative)
    if expected is None:
        return False, 0
    parent = open_scoped_directory(anchor, relative[:-1])
    if parent is None:
        return False, 0
    target: int | None = None
    try:
        try:
            target = _open_matching_directory(parent, relative[-1], expected)
        except FileNotFoundError:
            return False, 0
        removed_files = _remove_directory_contents(target)
        if _current_identity(parent, relative[-1]) != expected:
            raise OffloadSecurityError(reason="offload cleanup target changed")
        try:
            os.rmdir(relative[-1], dir_fd=parent)
        except FileNotFoundError as error:
            raise OffloadSecurityError(reason="offload cleanup target changed") from error
        return True, removed_files
    finally:
        if target is not None:
            with suppress(OSError):
                os.close(target)
        os.close(parent)


def remove_scoped_tree_with_status(anchor: Path, relative: tuple[str, ...]) -> tuple[bool, int]:
    """Remove a tree in place; only one serialized caller reports the win."""
    expected = preflight_scoped_tree(anchor, relative)
    with conversation_cleanup_lock(anchor, relative):
        return remove_preflighted_scoped_tree_with_status_locked(anchor, relative, expected)


def remove_scoped_tree(anchor: Path, relative: tuple[str, ...]) -> int:
    """Remove one scoped tree and return its exact regular-file count."""
    _removed, files = remove_scoped_tree_with_status(anchor, relative)
    return files


def remove_scoped_tree_locked(anchor: Path, relative: tuple[str, ...]) -> int:
    """Remove one tree while the caller owns the conversation mutation lock."""
    _removed, files = remove_scoped_tree_with_status_locked(anchor, relative)
    return files
