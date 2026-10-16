"""Deep Agents backend for prepared leases (parent Task 10, internal I12).

``LeaseSnapshotBackend`` serves immutable reads from a sealed
``RuntimeSnapshotCatalog`` (remapped virtual paths, no file or row behind
them) and allows writes only under the exact run-owned writable roots
(scratch / offload / output) given at preparation. Every writable root must
resolve strictly below one ``run_root`` that is disjoint from (not equal to,
inside, or containing) the shared data root, and no two writable roots may
nest. No backend is mapped to the shared data root, so it and every path
escape are unreachable: snapshot paths are a closed dict lookup, and the
writable routes are ``virtual_mode`` filesystem backends that reject
``..``/``~`` and resolved escapes.

Opt-in only: production preparation wires it in a later internal task.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from deepagents.backends.protocol import (
    PERMISSION_DENIED,
    BackendProtocol,
    DeleteResult,
    EditResult,
    FileData,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from app.agent_runtime.runtime_snapshot_catalog import (
    RuntimeSnapshotCatalog,
    normalize_virtual_path,
)

READ_ONLY_ERROR = "permission_denied: prepared snapshot paths are read-only"
# Escape refusals (ValueError) and path-resolution failures (OSError, and
# RuntimeError for symlink loops on Python 3.12) are error results, never raises.
_REFUSALS = (ValueError, OSError, RuntimeError)


class LeaseSnapshotBackendError(ValueError):
    def __init__(self, code: str, subject: str) -> None:
        super().__init__(f"{code}:{subject}")
        self.code = code


class FrozenSnapshotBackend(StateBackend):
    """Read-only view over sealed snapshot bytes using the installed state semantics."""

    def __init__(self, catalog: RuntimeSnapshotCatalog) -> None:
        super().__init__()
        self._catalog = catalog
        self._files: Mapping[str, FileData] = catalog.file_data()

    def _read_files(self) -> dict[str, Any]:
        return {path: dict(data) for path, data in self._files.items()}

    def _send_files_update(self, update: dict[str, Any]) -> None:
        raise PermissionError(READ_ONLY_ERROR)

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=READ_ONLY_ERROR)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002 - installed signature
    ) -> EditResult:
        return EditResult(error=READ_ONLY_ERROR)

    def delete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error=READ_ONLY_ERROR)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error=PERMISSION_DENIED) for path, _ in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            content = self._catalog.read_bytes(path)
            if content is None:
                responses.append(FileDownloadResponse(path=path, error="file_not_found"))
            else:
                responses.append(FileDownloadResponse(path=path, content=content))
        return responses


class RunRootBackend(FilesystemBackend):
    """``virtual_mode`` run root whose refusals/OS errors are results, never raises."""

    def __init__(self, root: Path) -> None:
        super().__init__(root_dir=root, virtual_mode=True)

    def ls(self, path: str) -> LsResult:
        try:
            return super().ls(path)
        except _REFUSALS:
            return LsResult(error=PERMISSION_DENIED)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        try:
            return super().read(file_path, offset, limit)
        except _REFUSALS:
            return ReadResult(error=PERMISSION_DENIED)

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            return super().write(file_path, content)
        except _REFUSALS:
            return WriteResult(error=PERMISSION_DENIED)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002 - installed signature
    ) -> EditResult:
        try:
            return super().edit(file_path, old_string, new_string, replace_all)
        except _REFUSALS:
            return EditResult(error=PERMISSION_DENIED)

    def delete(self, file_path: str) -> DeleteResult:
        try:
            return super().delete(file_path)
        except _REFUSALS:
            return DeleteResult(error=PERMISSION_DENIED)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        try:
            return super().glob(pattern, path)
        except _REFUSALS:
            return GlobResult(error=PERMISSION_DENIED)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        try:
            return super().grep(pattern, path, glob, max_count=max_count)
        except _REFUSALS:
            return GrepResult(error=PERMISSION_DENIED)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        try:
            return await super().agrep(pattern, path, glob, max_count=max_count)
        except _REFUSALS:
            return GrepResult(error=PERMISSION_DENIED)


def _route_prefix(virtual_prefix: str) -> str:
    return normalize_virtual_path(virtual_prefix.rstrip("/")) + "/"


def _directory(physical: Path) -> Path:
    if physical.is_symlink() or not physical.is_dir():
        raise LeaseSnapshotBackendError("lease_backend_root_invalid", str(physical))
    return physical.resolve()


def _checked_run_root(run_root: Path, forbidden_root: Path) -> Path:
    resolved = _directory(run_root)
    forbidden = forbidden_root.resolve()
    if resolved.is_relative_to(forbidden) or forbidden.is_relative_to(resolved):
        raise LeaseSnapshotBackendError("lease_backend_root_shared", str(run_root))
    return resolved


def _checked_root(physical: Path, run_root: Path) -> Path:
    resolved = _directory(physical)
    if resolved == run_root or not resolved.is_relative_to(run_root):
        raise LeaseSnapshotBackendError("lease_backend_root_outside_run", str(physical))
    return resolved


class LeaseSnapshotBackend(CompositeBackend):
    """Immutable snapshot reads plus exact run-root writes, nothing else."""

    def __init__(
        self,
        catalog: RuntimeSnapshotCatalog,
        *,
        run_root: Path,
        writable_roots: Mapping[str, Path],
        forbidden_root: Path,
        artifacts_root: str | None = None,
    ) -> None:
        routes: dict[str, BackendProtocol] = {}
        physical_roots: set[Path] = set()
        run = _checked_run_root(run_root, forbidden_root)
        for virtual_prefix, physical in writable_roots.items():
            prefix = _route_prefix(virtual_prefix)
            resolved = _checked_root(physical, run)
            if resolved in physical_roots or prefix in routes:
                raise LeaseSnapshotBackendError("lease_backend_root_duplicate", prefix)
            if any(
                resolved.is_relative_to(p) or p.is_relative_to(resolved) for p in physical_roots
            ):
                raise LeaseSnapshotBackendError("lease_backend_root_nested", prefix)
            if any(path.startswith(prefix) for path in catalog.entries):
                raise LeaseSnapshotBackendError("lease_backend_root_overlaps_snapshot", prefix)
            physical_roots.add(resolved)
            routes[prefix] = RunRootBackend(resolved)
        for prefix in routes:
            if any(other != prefix and other.startswith(prefix) for other in routes):
                raise LeaseSnapshotBackendError("lease_backend_root_nested", prefix)
        root = _route_prefix(artifacts_root).rstrip("/") if artifacts_root else "/"
        if artifacts_root and f"{root}/" not in routes:
            raise LeaseSnapshotBackendError("lease_backend_artifacts_root_unwritable", root)
        super().__init__(
            default=FrozenSnapshotBackend(catalog),
            routes=routes,
            artifacts_root=root,
        )
        self.catalog = catalog
        self.writable_prefixes: frozenset[str] = frozenset(routes)
