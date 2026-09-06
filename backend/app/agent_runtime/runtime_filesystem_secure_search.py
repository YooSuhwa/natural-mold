"""No-follow directory traversal for model-visible runtime filesystem tools."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from deepagents.backends.protocol import FileInfo, GlobResult, GrepMatch, GrepResult, LsResult
from deepagents.backends.utils import compile_grep_include_glob

from app.agent_runtime.runtime_filesystem_secure_fd import (
    FilesystemIdentityChanged,
    open_private_regular,
    open_verified_child_directory,
    open_verified_directory,
)


def _virtual(base: str, relative: tuple[str, ...]) -> str:
    prefix = base.rstrip("/")
    return f"{prefix}/{'/'.join(relative)}" if prefix else f"/{'/'.join(relative)}"


def _open_directory(root: Path, parts: tuple[str, ...]) -> int | None:
    return open_verified_directory(root, parts, create=False)


def _regular(
    parent: int,
    name: str,
    expected: os.stat_result | None = None,
) -> tuple[int, os.stat_result] | None:
    try:
        return open_private_regular(parent, name, expected)
    except FilesystemIdentityChanged:
        raise
    except (IsADirectoryError, OSError, ValueError):
        return None


def _walk(
    descriptor: int,
    excluded_top_level: frozenset[str],
    prefix: tuple[str, ...] = (),
) -> Iterator[tuple[tuple[str, ...], int, os.stat_result]]:
    try:
        names = sorted(os.listdir(descriptor))  # noqa: PTH208 -- dirfd pins the directory
    except OSError:
        return
    for name in names:
        if not prefix and name in excluded_top_level:
            continue
        relative = (*prefix, name)
        try:
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISDIR(metadata.st_mode):
            try:
                child, _opened = open_verified_child_directory(descriptor, name, metadata)
            except FilesystemIdentityChanged:
                raise
            except OSError:
                continue
            try:
                yield from _walk(child, excluded_top_level, relative)
            finally:
                os.close(child)
            continue
        opened = _regular(descriptor, name, metadata)
        if opened is not None:
            file_descriptor, opened_metadata = opened
            yield relative, file_descriptor, opened_metadata


def secure_ls(
    root: Path,
    path: str,
    parts: tuple[str, ...],
    excluded_top_level: frozenset[str],
) -> LsResult:
    try:
        descriptor = _open_directory(root, parts)
    except (OSError, ValueError):
        descriptor = None
    if descriptor is None:
        return LsResult(error="filesystem access denied")
    entries: list[FileInfo] = []
    try:
        for name in sorted(os.listdir(descriptor)):  # noqa: PTH208 -- dirfd pins the directory
            if not parts and name in excluded_top_level:
                continue
            try:
                metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except OSError:
                continue
            child_path = _virtual(path, (name,))
            if stat.S_ISDIR(metadata.st_mode):
                try:
                    child, opened_metadata = open_verified_child_directory(
                        descriptor, name, metadata
                    )
                except FilesystemIdentityChanged:
                    raise
                except OSError:
                    continue
                os.close(child)
                entries.append(
                    {
                        "path": f"{child_path}/",
                        "is_dir": True,
                        "size": 0,
                        "modified_at": datetime.fromtimestamp(
                            opened_metadata.st_mtime, UTC
                        ).isoformat(),
                    }
                )
                continue
            opened = _regular(descriptor, name, metadata)
            if opened is None:
                continue
            file_descriptor, opened_metadata = opened
            os.close(file_descriptor)
            entries.append(
                {
                    "path": child_path,
                    "is_dir": False,
                    "size": opened_metadata.st_size,
                    "modified_at": datetime.fromtimestamp(
                        opened_metadata.st_mtime, UTC
                    ).isoformat(),
                }
            )
    except OSError:
        return LsResult(error="filesystem access denied")
    finally:
        os.close(descriptor)
    return LsResult(entries=entries)


def secure_glob(
    root: Path,
    path: str,
    parts: tuple[str, ...],
    pattern: str,
    excluded_top_level: frozenset[str],
) -> GlobResult:
    try:
        matcher = compile_grep_include_glob(pattern)
    except ValueError:
        return GlobResult(error="filesystem access denied", matches=[])
    descriptor = _open_directory(root, parts)
    if descriptor is None:
        return GlobResult(error="filesystem access denied", matches=[])
    matches: list[FileInfo] = []
    try:
        for relative, file_descriptor, metadata in _walk(descriptor, excluded_top_level):
            os.close(file_descriptor)
            relative_path = "/".join(relative)
            if matcher(relative_path):
                matches.append(
                    {
                        "path": _virtual(path, relative),
                        "is_dir": False,
                        "size": metadata.st_size,
                        "modified_at": datetime.fromtimestamp(metadata.st_mtime, UTC).isoformat(),
                    }
                )
    finally:
        os.close(descriptor)
    return GlobResult(matches=matches)


def secure_grep(
    root: Path,
    path: str,
    parts: tuple[str, ...],
    pattern: str,
    include_glob: str | None,
    max_count: int | None,
    excluded_top_level: frozenset[str],
) -> GrepResult:
    try:
        matcher = compile_grep_include_glob(include_glob) if include_glob else None
    except ValueError:
        return GrepResult(error="filesystem access denied", matches=[])
    try:
        descriptor = _open_directory(root, parts)
    except (OSError, ValueError):
        descriptor = None
    if descriptor is None:
        parent = _open_directory(root, parts[:-1]) if parts else None
        if parent is None:
            return GrepResult(error="filesystem access denied", matches=[])
        try:
            opened = _regular(parent, parts[-1])
            if opened is None:
                return GrepResult(error="filesystem access denied", matches=[])
            file_descriptor, _metadata = opened
            if matcher is not None and not matcher(parts[-1]):
                os.close(file_descriptor)
                return GrepResult(matches=[])
            matches: list[GrepMatch] = []
            truncated = False
            try:
                with os.fdopen(file_descriptor, "r", encoding="utf-8") as stream:
                    for line_number, line in enumerate(stream, 1):
                        if pattern in line:
                            if max_count is not None and len(matches) >= max_count:
                                truncated = max_count >= 0
                                break
                            matches.append(
                                {
                                    "path": path,
                                    "line": line_number,
                                    "text": line.rstrip("\n"),
                                }
                            )
            except UnicodeDecodeError:
                return GrepResult(matches=[])
            return GrepResult(matches=matches, truncated=truncated)
        finally:
            os.close(parent)
    matches: list[GrepMatch] = []
    truncated = False
    try:
        for relative, file_descriptor, _metadata in _walk(descriptor, excluded_top_level):
            relative_path = "/".join(relative)
            if matcher is not None and not matcher(relative_path):
                os.close(file_descriptor)
                continue
            try:
                with os.fdopen(file_descriptor, "r", encoding="utf-8") as stream:
                    for line_number, line in enumerate(stream, 1):
                        if pattern not in line:
                            continue
                        if max_count is not None and len(matches) >= max_count:
                            truncated = max_count >= 0
                            break
                        matches.append(
                            {
                                "path": _virtual(path, relative),
                                "line": line_number,
                                "text": line.rstrip("\n"),
                            }
                        )
            except UnicodeDecodeError:
                continue
            if truncated:
                break
    finally:
        os.close(descriptor)
    return GrepResult(matches=matches, truncated=truncated)


__all__ = ["secure_glob", "secure_grep", "secure_ls"]
