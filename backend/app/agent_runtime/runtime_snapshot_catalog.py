"""Immutable, content-addressed preparation snapshot (parent Task 10, internal I12).

Before the marker, the preparer copies every byte a lease may read — selected
skill trees, ``AGENTS.md``/memory sources, allowed application resource bytes
and allowed history — into a ``RuntimeSnapshotCatalog``. After ``build()``
nothing refers back to the source files or rows, so later DB/file/source
mutation cannot change what the run reads. Source files must be regular,
non-symlinked, single-link files inside the given source root, and the opened
inode must match the ``lstat`` taken after the checks (no swap before open);
virtual paths are absolute, normalized and traversal-free.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Literal

from deepagents.backends.protocol import FileData

SnapshotKind = Literal["skill", "memory", "resource", "history"]
RESOURCE_ROOT = "/resources"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


class SnapshotCatalogError(ValueError):
    def __init__(self, code: str, subject: str) -> None:
        super().__init__(f"{code}:{subject}")
        self.code = code
        self.subject = subject


def normalize_virtual_path(path: str) -> str:
    """Return a canonical absolute virtual path or raise ``SnapshotCatalogError``."""

    if not path.startswith("/") or "\x00" in path or "\\" in path or path.startswith("~"):
        raise SnapshotCatalogError("snapshot_path_invalid", path)
    parts = PurePosixPath(path).parts[1:]
    canonical = "/" + "/".join(parts)
    if not parts or canonical != path or any(part in ("..", ".") for part in parts):
        raise SnapshotCatalogError("snapshot_path_invalid", path)
    return canonical


@dataclass(frozen=True, slots=True)
class SnapshotEntry:
    virtual_path: str
    kind: SnapshotKind
    sha256: str
    content: bytes = field(repr=False)

    @property
    def size(self) -> int:
        return len(self.content)


@dataclass(frozen=True, slots=True)
class RuntimeSnapshotCatalog:
    entries: Mapping[str, SnapshotEntry]
    resource_ids: frozenset[str]
    prepared_at: str
    digest: str

    def read_bytes(self, virtual_path: str) -> bytes | None:
        entry = self.entries.get(virtual_path)
        return None if entry is None else entry.content

    def file_data(self) -> dict[str, FileData]:
        """Fresh Deep Agents ``FileData`` copies (callers cannot mutate the catalog)."""

        files: dict[str, FileData] = {}
        for path, entry in self.entries.items():
            try:
                content, encoding = entry.content.decode("utf-8"), "utf-8"
            except UnicodeDecodeError:
                content, encoding = base64.b64encode(entry.content).decode("ascii"), "base64"
            files[path] = {
                "content": content,
                "encoding": encoding,
                "created_at": self.prepared_at,
                "modified_at": self.prepared_at,
            }
        return files

    def digests(self) -> dict[str, str]:
        """Non-secret ``snapshot_digests`` material for the lease digest."""

        return {path: entry.sha256 for path, entry in self.entries.items()}


def _read_regular_file(root: Path, source: Path) -> bytes:
    if root.is_symlink():
        raise SnapshotCatalogError("snapshot_source_symlink", str(root))
    root_resolved = root.resolve()
    candidate = source if source.is_absolute() else root / source
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        raise SnapshotCatalogError("snapshot_source_outside_root", str(source)) from None
    current = root
    for part in relative.parts:
        current = current / part
        if part in ("..", ".") or current.is_symlink():
            raise SnapshotCatalogError("snapshot_source_symlink", str(source))
    if not current.resolve().is_relative_to(root_resolved):
        raise SnapshotCatalogError("snapshot_source_outside_root", str(source))
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        checked = os.lstat(current)
        fd = os.open(current, flags)
    except OSError:
        raise SnapshotCatalogError("snapshot_source_unreadable", str(source)) from None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise SnapshotCatalogError("snapshot_source_not_regular", str(source))
        # The opened inode must be the one the symlink/containment checks saw.
        if (info.st_dev, info.st_ino) != (checked.st_dev, checked.st_ino):
            raise SnapshotCatalogError("snapshot_source_changed", str(source))
        # A hard link can alias bytes outside the root under an in-root name.
        if info.st_nlink > 1:
            raise SnapshotCatalogError("snapshot_source_hardlinked", str(source))
        data = handle.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise SnapshotCatalogError("snapshot_file_too_large", str(source))
    return data


class SnapshotCatalogBuilder:
    """Collects frozen copies during preparation; ``build()`` seals them."""

    def __init__(self) -> None:
        self._entries: dict[str, SnapshotEntry] = {}
        self._resource_ids: set[str] = set()
        self._total = 0
        self._sealed = False

    def add_bytes(self, virtual_path: str, data: bytes | bytearray, *, kind: SnapshotKind) -> str:
        if self._sealed:
            raise SnapshotCatalogError("snapshot_sealed", virtual_path)
        path = normalize_virtual_path(virtual_path)
        if path in self._entries:
            raise SnapshotCatalogError("snapshot_path_duplicate", path)
        content = bytes(data)
        if len(content) > MAX_FILE_BYTES:
            raise SnapshotCatalogError("snapshot_file_too_large", path)
        self._total += len(content)
        if self._total > MAX_TOTAL_BYTES:
            raise SnapshotCatalogError("snapshot_total_too_large", path)
        digest = hashlib.sha256(content).hexdigest()
        self._entries[path] = SnapshotEntry(path, kind, digest, content)
        return digest

    def add_file(self, virtual_path: str, *, root: Path, source: Path, kind: SnapshotKind) -> str:
        return self.add_bytes(virtual_path, _read_regular_file(root, source), kind=kind)

    def add_tree(self, virtual_prefix: str, *, root: Path, kind: SnapshotKind = "skill") -> int:
        """Freeze every regular file below ``root``; symlinks anywhere reject."""

        prefix = normalize_virtual_path(virtual_prefix)
        if root.is_symlink() or not root.is_dir():
            raise SnapshotCatalogError("snapshot_tree_invalid", str(root))
        count = 0
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            base = Path(dirpath)
            dirnames.sort()
            for name in [*dirnames, *filenames]:
                if (base / name).is_symlink():
                    raise SnapshotCatalogError("snapshot_source_symlink", str(base / name))
            for name in sorted(filenames):
                relative = (base / name).relative_to(root).as_posix()
                self.add_file(f"{prefix}/{relative}", root=root, source=base / name, kind=kind)
                count += 1
        return count

    def add_resource(self, resource_id: str, data: bytes | bytearray) -> str:
        """Freeze one allowed live application resource under ``/resources/<id>``."""

        if not resource_id or "/" in resource_id or resource_id in (".", ".."):
            raise SnapshotCatalogError("snapshot_resource_id_invalid", resource_id)
        digest = self.add_bytes(f"{RESOURCE_ROOT}/{resource_id}", data, kind="resource")
        self._resource_ids.add(resource_id)
        return digest

    def build(self) -> RuntimeSnapshotCatalog:
        self._sealed = True
        entries = dict(sorted(self._entries.items()))
        material = {path: [entry.kind, entry.sha256] for path, entry in entries.items()}
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        return RuntimeSnapshotCatalog(
            entries=MappingProxyType(entries),
            resource_ids=frozenset(self._resource_ids),
            prepared_at=datetime.now(UTC).isoformat(),
            digest=hashlib.sha256(encoded).hexdigest(),
        )
