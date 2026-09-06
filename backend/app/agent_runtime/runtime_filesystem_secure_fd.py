"""Verified dirfd primitives for model-visible runtime files."""

from __future__ import annotations

import os
import secrets
import stat
from contextlib import suppress
from pathlib import Path

from app.agent_runtime.filesystem_permissions import canonicalize_virtual_path
from app.agent_runtime.offload_storage_fd import directory_flags, nofollow_flags


class FilesystemIdentityChanged(PermissionError):
    """A filesystem name resolved to a different inode between inspection and open."""


def virtual_file_parts(path: str) -> tuple[str, ...]:
    canonical = canonicalize_virtual_path(path)
    parts = tuple(canonical.strip("/").split("/"))
    if not parts:
        raise PermissionError("runtime file path must name a file")
    return parts


def _verified_child_directory(
    parent: int,
    name: str,
    expected: os.stat_result,
) -> tuple[int, os.stat_result]:
    if not stat.S_ISDIR(expected.st_mode):
        raise NotADirectoryError(name)
    child = os.open(name, directory_flags(), dir_fd=parent)
    try:
        opened = os.fstat(child)
        if not stat.S_ISDIR(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            expected.st_dev,
            expected.st_ino,
        ):
            raise FilesystemIdentityChanged
    except BaseException:
        os.close(child)
        raise
    return child, opened


def open_verified_child_directory(
    parent: int,
    name: str,
    expected: os.stat_result | None = None,
) -> tuple[int, os.stat_result]:
    inspected = (
        os.stat(name, dir_fd=parent, follow_symlinks=False) if expected is None else expected
    )
    return _verified_child_directory(parent, name, inspected)


def open_verified_directory(
    root: Path,
    relative: tuple[str, ...],
    *,
    create: bool,
) -> int | None:
    absolute = root if root.is_absolute() else Path.cwd() / root
    descriptor = os.open(absolute.anchor, directory_flags())
    try:
        for component in (*absolute.parts[1:], *relative):
            try:
                inspected = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    os.close(descriptor)
                    return None
                with suppress(FileExistsError):
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                inspected = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            child, _metadata = _verified_child_directory(descriptor, component, inspected)
            os.close(descriptor)
            descriptor = child
    except BaseException:
        with suppress(OSError):
            os.close(descriptor)
        raise
    return descriptor


def open_private_regular(
    parent: int,
    name: str,
    expected: os.stat_result | None = None,
) -> tuple[int, os.stat_result] | None:
    try:
        inspected = (
            os.stat(name, dir_fd=parent, follow_symlinks=False) if expected is None else expected
        )
    except FileNotFoundError:
        return None
    try:
        descriptor = os.open(name, nofollow_flags(os.O_RDONLY), dir_fd=parent)
    except FileNotFoundError:
        raise FilesystemIdentityChanged from None
    try:
        opened = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        identities = {
            (inspected.st_dev, inspected.st_ino),
            (opened.st_dev, opened.st_ino),
            (current.st_dev, current.st_ino),
        }
        if len(identities) != 1:
            raise FilesystemIdentityChanged
        valid = (
            stat.S_ISREG(inspected.st_mode)
            and inspected.st_nlink == 1
            and stat.S_ISREG(opened.st_mode)
            and opened.st_nlink == 1
            and stat.S_ISREG(current.st_mode)
            and current.st_nlink == 1
        )
        if not valid:
            raise PermissionError("runtime file is not a private regular file")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, opened


def atomic_replace_private(
    parent: int,
    name: str,
    content: bytes,
    expected: tuple[int, int] | None,
) -> None:
    temporary = f".{name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(
        temporary,
        nofollow_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL),
        0o600,
        dir_fd=parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        current = open_private_regular(parent, name)
        if expected is None:
            if current is not None:
                os.close(current[0])
                raise PermissionError("runtime file changed during write")
        else:
            if current is None:
                raise PermissionError("runtime file changed during edit")
            current_descriptor, current_metadata = current
            os.close(current_descriptor)
            if (current_metadata.st_dev, current_metadata.st_ino) != expected:
                raise PermissionError("runtime file changed during edit")
        os.rename(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=parent)


__all__ = [
    "FilesystemIdentityChanged",
    "atomic_replace_private",
    "open_private_regular",
    "open_verified_child_directory",
    "open_verified_directory",
    "virtual_file_parts",
]
