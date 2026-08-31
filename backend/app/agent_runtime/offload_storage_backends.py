"""Filesystem adapters for scoped Deep Agents offload storage."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Final

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import FileDownloadResponse, ReadResult, WriteResult
from deepagents.backends.utils import slice_read_response

from app.agent_runtime.offload_storage_cleanup import conversation_mutation_lock
from app.agent_runtime.offload_storage_fd import directory_flags as _directory_flags
from app.agent_runtime.offload_storage_fd import open_scoped_directory as _open_scoped_directory
from app.agent_runtime.offload_storage_fd import raise_directory_error as _raise_directory_error
from app.agent_runtime.offload_storage_fd import (
    safe_dir,  # noqa: F401 -- compatibility export for the scoped storage facade
)
from app.agent_runtime.offload_storage_secure_backend import SecureHashedFilesystemBackend
from app.agent_runtime.offload_storage_types import (
    MEDIA_FILE,
    PHYSICAL_ROOT,
    SESSION_FILE,
    VIRTUAL_ROOT,
    OffloadMutationScope,
    OffloadSecurityError,
)

HIDDEN_ROOTS: Final = frozenset(
    {PHYSICAL_ROOT, VIRTUAL_ROOT.removeprefix("/"), "conversation_history", "large_tool_results"}
)


def parts(key: str) -> tuple[str, ...]:
    path = PurePosixPath(key)
    result = path.parts[1:] if path.is_absolute() else path.parts
    if not result or any(part in {"", ".", ".."} for part in result):
        raise PermissionError("invalid internal offload path")
    return result


class HiddenFilesystemBackend(FilesystemBackend):
    """Default data backend that makes both internal namespaces unreachable."""

    def _resolve_path(self, key: str) -> Path:
        key_parts = parts(key) if key not in {"", "/"} else ()
        if key_parts and key_parts[0] in HIDDEN_ROOTS:
            raise PermissionError("internal offload path is not publicly accessible")
        return super()._resolve_path(key)

    def _to_virtual_path(self, path: Path) -> str:
        relative = path.resolve().relative_to(self.cwd)
        if relative.parts and relative.parts[0] in HIDDEN_ROOTS:
            raise OffloadSecurityError(reason="internal offload path is hidden")
        return super()._to_virtual_path(path)


class HashedFilesystemBackend(SecureHashedFilesystemBackend):
    """Exact-reference backend whose raw virtual segments never reach disk."""

    def _validated_parts(self, key: str) -> tuple[str, ...]:
        return parts(key)


class HistoryBackend(HashedFilesystemBackend):
    domain = "history"

    def _validated_parts(self, key: str) -> tuple[str, ...]:
        key_parts = parts(key)
        valid_session = len(key_parts) == 1 and SESSION_FILE.fullmatch(key_parts[0]) is not None
        valid_media = (
            len(key_parts) == 2
            and key_parts[0] == "media"
            and MEDIA_FILE.fullmatch(key_parts[1]) is not None
        )
        if not (valid_session or valid_media):
            raise OffloadSecurityError(reason="invalid durable history reference")
        return key_parts


class SpillBackend(HashedFilesystemBackend):
    domain = "spill"

    def __init__(
        self,
        current_root: Path,
        conversation_root: Path,
        actor: str,
        mutation_scope: OffloadMutationScope,
    ) -> None:
        super().__init__(
            root_dir=current_root,
            mutation_scope=mutation_scope,
            virtual_mode=True,
        )
        self._conversation_root = conversation_root
        self._actor = actor

    def _validated_parts(self, key: str) -> tuple[str, ...]:
        key_parts = parts(key)
        if len(key_parts) != 1:
            raise OffloadSecurityError(reason="invalid large-result reference")
        return key_parts

    def _prior_roots(self) -> tuple[Path, ...]:
        descriptor = _open_scoped_directory(self._conversation_root, ())
        if descriptor is None:
            return ()
        roots: list[tuple[int, Path]] = []
        try:
            for run_name in os.listdir(descriptor):  # noqa: PTH208 -- dirfd pins trusted root
                try:
                    run_descriptor = os.open(run_name, _directory_flags(), dir_fd=descriptor)
                except FileNotFoundError:
                    continue
                except OSError as error:
                    _raise_directory_error(error)
                else:
                    try:
                        try:
                            actor_descriptor = os.open(
                                self._actor, _directory_flags(), dir_fd=run_descriptor
                            )
                        except FileNotFoundError:
                            continue
                        except OSError as error:
                            _raise_directory_error(error)
                        try:
                            root = self._conversation_root / run_name / self._actor
                            if root != self.cwd:
                                roots.append((os.fstat(actor_descriptor).st_mtime_ns, root))
                        finally:
                            os.close(actor_descriptor)
                    finally:
                        os.close(run_descriptor)
        finally:
            os.close(descriptor)
        roots.sort(key=lambda item: item[0], reverse=True)
        return tuple(root for _modified, root in roots)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        current = super().read(file_path, offset=offset, limit=limit)
        if current.error is None:
            return current
        try:
            roots = self._prior_roots()
        except OffloadSecurityError:
            return current
        for root in roots:
            try:
                raw = self._read_bytes(file_path, root=root)
                content = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            return slice_read_response({"content": content, "encoding": "utf-8"}, offset, limit)
        return current

    def write(self, file_path: str, content: str) -> WriteResult:
        with conversation_mutation_lock(self._mutation_scope):
            return self._write_with_prior_check_locked(file_path, content)

    def _write_with_prior_check_locked(self, file_path: str, content: str) -> WriteResult:
        current = super().read(file_path)
        if current.error is None:
            return super()._write_locked(file_path, content)
        try:
            roots = self._prior_roots()
        except OffloadSecurityError:
            return WriteResult(error=current.error)
        for root in roots:
            try:
                prior = self._read_bytes(file_path, root=root)
            except (OSError, ValueError):
                continue
            if prior == content.encode():
                return WriteResult(path=file_path)
            return WriteResult(error="large-result reference collides with a prior run")
        return super()._write_locked(file_path, content)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [self._download_one(path) for path in paths]

    def _download_one(self, path: str) -> FileDownloadResponse:
        current = super().download_files([path])[0]
        if current.error is None:
            return current
        try:
            roots = self._prior_roots()
        except OffloadSecurityError:
            return current
        for root in roots:
            try:
                return FileDownloadResponse(path=path, content=self._read_bytes(path, root=root))
            except (OSError, ValueError):
                continue
        return current
