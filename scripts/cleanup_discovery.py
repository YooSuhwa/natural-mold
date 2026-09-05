"""Secure residue-only discovery for final isolation cleanup verification."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from cleanup_discovery_claims import Claims, JSONValue, classify_claim, merge_claims
from e2e_cleanup_contract import require
from e2e_cleanup_export_paths import _PinnedDirectory, _read_at
from postgres_cleanup_checker import ManifestValidationError

MAX_FILES: Final = 2_048
MAX_FILE_BYTES: Final = 20 * 1024 * 1024
MAX_TOTAL_BYTES: Final = 128 * 1024 * 1024
type FileIdentity = tuple[int, int, int, int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class DiscoverySummary:
    receipts: tuple[str, ...]


def _json_names(directory_fd: int) -> tuple[str, ...]:
    names: list[str] = []
    with os.scandir(directory_fd) as entries:
        for count, entry in enumerate(entries, start=1):
            require(count <= MAX_FILES, "discovery_limit")
            if entry.name.endswith(".json"):
                names.append(entry.name)
    return tuple(sorted(names))


def _file_identity(metadata: os.stat_result) -> FileIdentity:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_claims(
    root: Path,
    temp_parents: tuple[Path, ...],
    after_read: Callable[[], None] | None,
) -> tuple[Claims, tuple[str, ...]]:
    claims: list[Claims] = []
    receipts: list[str] = []
    identities: dict[str, FileIdentity] = {}
    total = 0
    with _PinnedDirectory(root) as pinned:
        root_metadata = os.fstat(pinned.fd)
        require(
            root_metadata.st_uid == os.getuid() and root_metadata.st_mode & 0o022 == 0,
            "discovery_root",
        )
        names = _json_names(pinned.fd)
        for name in names:
            metadata = os.stat(name, dir_fd=pinned.fd, follow_symlinks=False)
            identity = _file_identity(metadata)
            require(
                stat.S_ISREG(metadata.st_mode)
                and metadata.st_nlink == 1
                and metadata.st_uid == os.getuid()
                and metadata.st_mode & 0o022 == 0
                and metadata.st_size <= MAX_FILE_BYTES,
                "discovery_file",
            )
            raw = _read_at(pinned.fd, PurePosixPath(name), metadata)
            require(
                _file_identity(os.stat(name, dir_fd=pinned.fd, follow_symlinks=False)) == identity,
                "discovery_file_changed",
            )
            identities[name] = identity
            total += len(raw)
            require(total <= MAX_TOTAL_BYTES, "discovery_limit")
            try:
                decoded: JSONValue = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise ManifestValidationError("discovery_json") from error
            if not isinstance(decoded, dict):
                continue
            claim = classify_claim(decoded, temp_parents)
            if claim is not None:
                claims.append(claim)
                receipts.append(name)
        if after_read is not None:
            after_read()
        try:
            unchanged = all(
                _file_identity(os.stat(name, dir_fd=pinned.fd, follow_symlinks=False)) == identity
                for name, identity in identities.items()
            )
        except OSError as error:
            raise ManifestValidationError("discovery_file_changed") from error
        require(unchanged, "discovery_file_changed")
        try:
            pinned.validate("discovery_root_changed")
        except (OSError, ManifestValidationError) as error:
            raise ManifestValidationError("discovery_root_changed") from error
        require(names == _json_names(pinned.fd), "discovery_root_changed")
    return merge_claims(claims), tuple(receipts)


def discover_claims(
    root: Path,
    *,
    temp_parents: tuple[Path, ...],
    after_read: Callable[[], None] | None = None,
) -> tuple[Claims, DiscoverySummary]:
    """Securely parse direct lifecycle claims without validating historical outcomes."""
    try:
        claims, receipts = _read_claims(root, temp_parents, after_read)
    except OSError as error:
        raise ManifestValidationError("discovery_root") from error
    return claims, DiscoverySummary(receipts)
