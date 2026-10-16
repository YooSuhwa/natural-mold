from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict

type JsonValue = str | int | float | bool | None | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
type ContentType = Literal["source", "metadata"]

PROTECTED = PurePosixPath(".omo/evidence/company-administration/task10-root-authority")


class AuthorityArtifact(TypedDict):
    path: str
    sha256: str
    bytes: int
    content_type: ContentType


class ArtifactIOError(ValueError): ...


class ArtifactCommitted(ArtifactIOError):
    def __init__(self, artifact: AuthorityArtifact) -> None:
        super().__init__("output_committed_before_interruption")
        self.artifact = artifact


def canonical_json_bytes(value: Mapping[str, JsonValue]) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def canonical_relative(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or str(path) != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ArtifactIOError("path_not_canonical")
    return value


def _parent_fd(root: Path, relative: str) -> tuple[int, str]:
    parts = PurePosixPath(canonical_relative(relative)).parts
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd, parts[-1]
    except OSError:
        os.close(fd)
        raise


def read_regular(root: Path, relative: str) -> bytes:
    parent, name = _parent_fd(root.resolve(), relative)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise ArtifactIOError("artifact_not_regular")
            chunks: list[bytes] = []
            while chunk := os.read(fd, 1024 * 1024):
                chunks.append(chunk)
            after = os.fstat(fd)
            if (before.st_dev, before.st_ino, before.st_size) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
            ):
                raise ArtifactIOError("artifact_changed_during_read")
            return b"".join(chunks)
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def artifact_tuple(root: Path, relative: str, content_type: ContentType) -> AuthorityArtifact:
    data = read_regular(root, relative)
    return {
        "path": canonical_relative(relative),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "content_type": content_type,
    }


def adapt_legacy_tuple(
    root: Path,
    legacy: Mapping[str, JsonValue],
    content_type: ContentType,
    registered: AuthorityArtifact,
) -> AuthorityArtifact:
    if set(legacy) != {"path", "sha256", "bytes"}:
        raise ArtifactIOError("legacy_tuple_schema")
    raw, digest, size = legacy.get("path"), legacy.get("sha256"), legacy.get("bytes")
    if (
        not isinstance(raw, str)
        or not isinstance(digest, str)
        or not isinstance(size, int)
        or isinstance(size, bool)
    ):
        raise ArtifactIOError("legacy_tuple_type")
    absolute, actual = Path(raw), root.resolve()
    if not absolute.is_absolute() or absolute != Path(os.path.normpath(raw)):
        raise ArtifactIOError("legacy_path_not_normalized")
    try:
        relative = absolute.relative_to(actual).as_posix()
    except ValueError as exc:
        raise ArtifactIOError("legacy_path_outside_root") from exc
    derived = artifact_tuple(actual, relative, content_type)
    if derived != registered or digest != derived["sha256"] or size != derived["bytes"]:
        raise ArtifactIOError("legacy_tuple_not_registered")
    return derived


def write_public_output(
    root: Path,
    relative: str,
    data: bytes,
    content_type: ContentType = "metadata",
    phase_hook: Callable[[str], None] | None = None,
) -> AuthorityArtifact:
    target = PurePosixPath(canonical_relative(relative))
    if target == PROTECTED or PROTECTED in target.parents:
        raise ArtifactIOError("protected_output")
    parent, name = _parent_fd(root.resolve(), relative)
    parent_identity = os.fstat(parent)
    temporary = f".{name}.task10-{uuid.uuid4().hex}.tmp"
    temp_fd = -1
    linked = False
    try:
        exists = True
        try:
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            exists = False
        if exists:
            raise ArtifactIOError("output_exists")
        temp_fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        view = memoryview(data)
        while view:
            view = view[os.write(temp_fd, view) :]
        os.fsync(temp_fd)
        info = os.fstat(temp_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ArtifactIOError("temporary_inode_invalid")
        if phase_hook:
            phase_hook("temp_fsynced")
        current_parent, _ = _parent_fd(root.resolve(), relative)
        try:
            current_identity = os.fstat(current_parent)
        finally:
            os.close(current_parent)
        if (parent_identity.st_dev, parent_identity.st_ino) != (
            current_identity.st_dev,
            current_identity.st_ino,
        ):
            raise ArtifactIOError("output_parent_changed")
        os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        linked = True
        if phase_hook:
            phase_hook("linked")
        os.fsync(parent)
        if phase_hook:
            phase_hook("parent_fsynced")
        os.unlink(temporary, dir_fd=parent)
        os.fsync(parent)
        return artifact_tuple(root, relative, content_type)
    except (ArtifactIOError, OSError, RuntimeError) as exc:
        created = temp_fd >= 0
        if created:
            os.close(temp_fd)
            temp_fd = -1
            os.unlink(temporary, dir_fd=parent)
            os.fsync(parent)
        if linked:
            try:
                committed = artifact_tuple(root, relative, content_type)
            except (ArtifactIOError, OSError):
                raise ArtifactIOError("committed_output_not_at_registered_path") from exc
            raise ArtifactCommitted(committed) from exc
        raise
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        os.close(parent)
