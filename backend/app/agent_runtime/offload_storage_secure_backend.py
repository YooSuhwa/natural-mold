"""Exact-reference filesystem backend whose internal I/O is dirfd-confined."""

from __future__ import annotations

import base64
import os
import secrets
import stat
from contextlib import suppress
from pathlib import Path

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import (
    _get_backend_read_file_type,
    check_empty_content,
    perform_string_replacement,
    slice_read_response,
)

from app.agent_runtime.offload_storage_cleanup import conversation_mutation_lock
from app.agent_runtime.offload_storage_fd import (
    nofollow_flags,
    open_directory_components,
    open_scoped_directory,
)
from app.agent_runtime.offload_storage_types import (
    OffloadMutationScope,
    OffloadSecurityError,
    token,
)


def _validate_single_link_regular_file(descriptor: int, parent: int, name: str) -> os.stat_result:
    """Require the opened file and current directory entry to be one private inode."""
    opened = os.fstat(descriptor)
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    private_regular = (
        stat.S_ISREG(opened.st_mode)
        and opened.st_nlink == 1
        and stat.S_ISREG(current.st_mode)
        and current.st_nlink == 1
        and (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)
    )
    if not private_regular:
        raise OffloadSecurityError(reason="offload reference must be a private regular file")
    return opened


def _open_private_file(parent: int, name: str) -> tuple[int, os.stat_result] | None:
    try:
        descriptor = os.open(name, nofollow_flags(os.O_RDONLY), dir_fd=parent)
    except FileNotFoundError:
        return None
    try:
        metadata = _validate_single_link_regular_file(descriptor, parent, name)
    except (OSError, ValueError):
        os.close(descriptor)
        raise
    return descriptor, metadata


def _atomic_replace_private_file(
    parent: int,
    name: str,
    content: bytes,
    *,
    expected: tuple[int, int] | None = None,
) -> None:
    current = _open_private_file(parent, name)
    if expected is not None:
        if current is None:
            raise OffloadSecurityError(reason="offload reference changed during edit")
        current_descriptor, current_metadata = current
        os.close(current_descriptor)
        if (current_metadata.st_dev, current_metadata.st_ino) != expected:
            raise OffloadSecurityError(reason="offload reference changed during edit")
    elif current is not None:
        os.close(current[0])

    temporary = f".{name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    flags = nofollow_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        descriptor = os.open(temporary, flags, 0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

        before_replace = _open_private_file(parent, name)
        if expected is None:
            if current is None and before_replace is not None:
                os.close(before_replace[0])
                raise OffloadSecurityError(reason="offload reference changed during write")
        else:
            if before_replace is None:
                raise OffloadSecurityError(reason="offload reference changed during edit")
            before_descriptor, before_metadata = before_replace
            os.close(before_descriptor)
            if (before_metadata.st_dev, before_metadata.st_ino) != expected:
                raise OffloadSecurityError(reason="offload reference changed during edit")
        if expected is None and before_replace is not None:
            os.close(before_replace[0])

        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=parent)


class SecureHashedFilesystemBackend(FilesystemBackend):
    """Map exact virtual paths to opaque leaves without pathname-based internal I/O."""

    domain: str

    def __init__(
        self,
        root_dir: Path,
        *,
        mutation_scope: OffloadMutationScope,
        virtual_mode: bool,
    ) -> None:
        super().__init__(root_dir=root_dir, virtual_mode=virtual_mode)
        self._mutation_scope = mutation_scope

    def _validated_parts(self, key: str) -> tuple[str, ...]:
        raise NotImplementedError

    def _mapped_parts(self, key: str) -> tuple[str, ...]:
        try:
            key_parts = self._validated_parts(key)
        except OffloadSecurityError as error:
            raise PermissionError(str(error)) from None
        return tuple(token(f"{self.domain}-path", part) for part in key_parts)

    def _resolve_path(self, key: str) -> Path:
        mapped = self._mapped_parts(key)
        descriptor = open_scoped_directory(self.cwd, ())
        if descriptor is None:
            raise OffloadSecurityError(reason="offload scope is unavailable")
        os.close(descriptor)
        return self.cwd.joinpath(*mapped)

    def _open_parent(self, key: str, *, create: bool) -> tuple[int, str]:
        mapped = self._mapped_parts(key)
        descriptor = open_directory_components(self.cwd, mapped[:-1], create=create)
        if descriptor is None:
            raise FileNotFoundError(key)
        return descriptor, mapped[-1]

    def _read_bytes(self, key: str, *, root: Path | None = None) -> bytes:
        mapped = self._mapped_parts(key)
        parent = open_directory_components(
            self.cwd if root is None else root, mapped[:-1], create=False
        )
        if parent is None:
            raise FileNotFoundError(key)
        try:
            descriptor = os.open(mapped[-1], nofollow_flags(os.O_RDONLY), dir_fd=parent)
            with os.fdopen(descriptor, "rb") as stream:
                _validate_single_link_regular_file(stream.fileno(), parent, mapped[-1])
                return stream.read()
        finally:
            os.close(parent)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        try:
            raw = self._read_bytes(file_path)
            if _get_backend_read_file_type(file_path) != "text":
                return ReadResult(
                    file_data={
                        "content": base64.standard_b64encode(raw).decode("ascii"),
                        "encoding": "base64",
                    }
                )
            content = raw.decode("utf-8")
            empty = check_empty_content(content)
            if empty:
                return ReadResult(file_data={"content": empty, "encoding": "utf-8"})
            return slice_read_response({"content": content, "encoding": "utf-8"}, offset, limit)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            return ReadResult(error=f"Error reading file '{file_path}': {error}")

    def write(self, file_path: str, content: str) -> WriteResult:
        with conversation_mutation_lock(self._mutation_scope):
            return self._write_locked(file_path, content)

    def _write_locked(self, file_path: str, content: str) -> WriteResult:
        try:
            parent, name = self._open_parent(file_path, create=True)
            try:
                _atomic_replace_private_file(parent, name, content.encode("utf-8"))
            finally:
                os.close(parent)
            return WriteResult(path=file_path)
        except (OSError, UnicodeEncodeError, ValueError) as error:
            return WriteResult(error=f"Error writing file '{file_path}': {error}")

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        with conversation_mutation_lock(self._mutation_scope):
            responses: list[FileUploadResponse] = []
            for file_path, content in files:
                try:
                    parent, name = self._open_parent(file_path, create=True)
                    try:
                        _atomic_replace_private_file(parent, name, content)
                    finally:
                        os.close(parent)
                    responses.append(FileUploadResponse(path=file_path))
                except (OSError, ValueError) as error:
                    responses.append(FileUploadResponse(path=file_path, error=str(error)))
            return responses

    def edit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        with conversation_mutation_lock(self._mutation_scope):
            try:
                parent, name = self._open_parent(file_path, create=False)
                try:
                    descriptor = os.open(name, nofollow_flags(os.O_RDONLY), dir_fd=parent)
                    with os.fdopen(descriptor, "r", encoding="utf-8", newline="") as stream:
                        metadata = _validate_single_link_regular_file(stream.fileno(), parent, name)
                        replacement = perform_string_replacement(
                            stream.read(),
                            old_string.replace("\r\n", "\n").replace("\r", "\n"),
                            new_string.replace("\r\n", "\n").replace("\r", "\n"),
                            replace_all,
                        )
                        if isinstance(replacement, str):
                            return EditResult(error=replacement)
                        content, occurrences = replacement
                    _atomic_replace_private_file(
                        parent,
                        name,
                        content.encode("utf-8"),
                        expected=(metadata.st_dev, metadata.st_ino),
                    )
                finally:
                    os.close(parent)
                return EditResult(path=file_path, occurrences=occurrences)
            except (OSError, UnicodeError, ValueError) as error:
                return EditResult(error=f"Error editing file '{file_path}': {error}")

    def delete(self, file_path: str) -> DeleteResult:
        with conversation_mutation_lock(self._mutation_scope):
            return self._delete_locked(file_path)

    def _delete_locked(self, file_path: str) -> DeleteResult:
        try:
            parent, name = self._open_parent(file_path, create=False)
            try:
                descriptor = os.open(name, nofollow_flags(os.O_RDONLY), dir_fd=parent)
                try:
                    _validate_single_link_regular_file(descriptor, parent, name)
                    os.unlink(name, dir_fd=parent)
                finally:
                    os.close(descriptor)
            finally:
                os.close(parent)
            return DeleteResult(path=file_path)
        except (OSError, ValueError) as error:
            return DeleteResult(error=f"Error deleting '{file_path}': {error}")

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                responses.append(FileDownloadResponse(path=path, content=self._read_bytes(path)))
            except (OSError, ValueError) as error:
                responses.append(FileDownloadResponse(path=path, error=str(error)))
        return responses
