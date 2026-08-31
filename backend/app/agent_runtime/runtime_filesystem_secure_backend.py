"""Dirfd-confined filesystem backend for model-visible runtime files."""

from __future__ import annotations

import base64
import os

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import (
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import (
    MAX_VIDEO_INPUT_BYTES,
    _get_backend_read_file_type,
    check_empty_content,
    perform_string_replacement,
    slice_read_response,
)

from app.agent_runtime.runtime_filesystem_secure_fd import (
    atomic_replace_private,
    open_verified_directory,
    virtual_file_parts,
)
from app.agent_runtime.runtime_filesystem_secure_fd import (
    open_private_regular as _open_existing,
)
from app.agent_runtime.runtime_filesystem_secure_search import secure_glob, secure_grep, secure_ls


class SecureRuntimeFilesystemBackend(FilesystemBackend):
    """Filesystem backend whose file reads and mutations never follow links."""

    _moldy_dirfd_safe = True
    _moldy_hidden_roots: frozenset[str] = frozenset()

    def _validated_parts(self, file_path: str) -> tuple[str, ...]:
        return virtual_file_parts(file_path)

    def _directory_parts(self, path: str) -> tuple[str, ...]:
        return () if path == "/" else self._validated_parts(path)

    def _open_parent(self, file_path: str, *, create: bool) -> tuple[int, str]:
        parts = self._validated_parts(file_path)
        parent = open_verified_directory(self.cwd, parts[:-1], create=create)
        if parent is None:
            raise FileNotFoundError(file_path)
        return parent, parts[-1]

    def _read_bytes(self, file_path: str) -> tuple[bytes, os.stat_result]:
        parent, name = self._open_parent(file_path, create=False)
        try:
            opened = _open_existing(parent, name)
            if opened is None:
                raise FileNotFoundError(file_path)
            descriptor, metadata = opened
            with os.fdopen(descriptor, "rb") as stream:
                return stream.read(), metadata
        finally:
            os.close(parent)

    def _write_bytes(self, file_path: str, content: bytes) -> None:
        parent, name = self._open_parent(file_path, create=True)
        try:
            current = _open_existing(parent, name)
            expected = None
            if current is not None:
                descriptor, metadata = current
                os.close(descriptor)
                expected = (metadata.st_dev, metadata.st_ino)
            atomic_replace_private(parent, name, content, expected)
        finally:
            os.close(parent)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        try:
            raw, metadata = self._read_bytes(file_path)
            file_type = _get_backend_read_file_type(file_path)
            if file_type != "text":
                if file_type == "video" and metadata.st_size > MAX_VIDEO_INPUT_BYTES:
                    return ReadResult(error="filesystem access denied")
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
        except (OSError, UnicodeDecodeError, ValueError):
            return ReadResult(error="filesystem access denied")

    def write(self, file_path: str, content: str) -> WriteResult:
        try:
            self._write_bytes(file_path, content.encode("utf-8"))
            return WriteResult(path=file_path)
        except (OSError, UnicodeEncodeError, ValueError):
            return WriteResult(error="filesystem access denied")

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        try:
            parent, name = self._open_parent(file_path, create=False)
            try:
                current = _open_existing(parent, name)
                if current is None:
                    return EditResult(error="filesystem access denied")
                descriptor, metadata = current
                with os.fdopen(descriptor, "r", encoding="utf-8", newline="") as stream:
                    replacement = perform_string_replacement(
                        stream.read(),
                        old_string.replace("\r\n", "\n").replace("\r", "\n"),
                        new_string.replace("\r\n", "\n").replace("\r", "\n"),
                        replace_all,
                    )
                if isinstance(replacement, str):
                    return EditResult(error=replacement)
                content, occurrences = replacement
                atomic_replace_private(
                    parent,
                    name,
                    content.encode("utf-8"),
                    (metadata.st_dev, metadata.st_ino),
                )
            finally:
                os.close(parent)
            return EditResult(path=file_path, occurrences=occurrences)
        except (OSError, UnicodeError, ValueError):
            return EditResult(error="filesystem access denied")

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                content, _metadata = self._read_bytes(path)
                responses.append(FileDownloadResponse(path=path, content=content))
            except FileNotFoundError:
                responses.append(FileDownloadResponse(path=path, error="file_not_found"))
            except (OSError, ValueError):
                responses.append(FileDownloadResponse(path=path, error="permission_denied"))
        return responses

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                self._write_bytes(path, content)
                responses.append(FileUploadResponse(path=path))
            except (OSError, ValueError):
                responses.append(FileUploadResponse(path=path, error="permission_denied"))
        return responses

    def ls(self, path: str) -> LsResult:
        try:
            parts = self._directory_parts(path)
            return secure_ls(self.cwd, path, parts, self._moldy_hidden_roots)
        except (OSError, ValueError):
            return LsResult(error="filesystem access denied")

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        try:
            base = path or "/"
            parts = self._directory_parts(base)
            return secure_glob(self.cwd, base, parts, pattern, self._moldy_hidden_roots)
        except (OSError, ValueError):
            return GlobResult(error="filesystem access denied", matches=[])

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
        context_lines: int = 0,
    ) -> GrepResult:
        if context_lines:
            return GrepResult(error="filesystem access denied", matches=[])
        try:
            base = path or "/"
            parts = self._directory_parts(base)
            return secure_grep(
                self.cwd,
                base,
                parts,
                pattern,
                glob,
                max_count,
                self._moldy_hidden_roots,
            )
        except (OSError, ValueError):
            return GrepResult(error="filesystem access denied", matches=[])


__all__ = ["SecureRuntimeFilesystemBackend"]
