"""No-follow, no-clobber file I/O for internal offload storage."""

from __future__ import annotations

import fcntl
import os
import secrets
import stat
from collections.abc import Iterable
from contextlib import suppress
from errno import ELOOP, ENOTDIR
from pathlib import Path
from uuid import UUID

from app.agent_runtime.offload_storage_fd import nofollow_flags
from app.agent_runtime.offload_storage_fd import open_scoped_directory as _open_scoped_directory
from app.agent_runtime.offload_storage_lock import check_conversation_offloads_active
from app.agent_runtime.offload_storage_types import (
    PHYSICAL_ROOT,
    ROLLBACK_SESSION_FILE,
    OffloadMutationScope,
    OffloadSecurityError,
    parse_actor,
    token,
)


def _validate_private_regular_file(descriptor: int, parent: int, name: str, reason: str) -> None:
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
        raise OffloadSecurityError(reason=reason)


def _read_private_file(parent: int, name: str, expected_length: int) -> bytes | None:
    try:
        descriptor = os.open(name, nofollow_flags(os.O_RDONLY), dir_fd=parent)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno in {ELOOP, ENOTDIR}:
            raise OffloadSecurityError(
                reason="offload destination must be a private regular file"
            ) from error
        raise
    with os.fdopen(descriptor, "rb") as stream:
        _validate_private_regular_file(
            stream.fileno(),
            parent,
            name,
            "offload destination must be a private regular file",
        )
        return stream.read(expected_length + 1)


def atomic_write_new_or_equal(path: Path, content: bytes) -> None:
    """Install content once, or verify an existing regular file is identical."""
    parent = _open_scoped_directory(path.parent, ())
    if parent is None:
        raise OffloadSecurityError(reason="offload destination directory is unavailable")
    temporary = f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    flags = nofollow_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        fcntl.flock(parent, fcntl.LOCK_EX)
        try:
            existing = _read_private_file(parent, path.name, len(content))
            if existing is not None:
                if existing != content:
                    raise OffloadSecurityError(reason="offload destination conflicts")
                return
            descriptor = os.open(temporary, flags, 0o600, dir_fd=parent)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            raced = _read_private_file(parent, path.name, len(content))
            if raced is not None:
                if raced != content:
                    raise OffloadSecurityError(reason="offload destination conflicts")
                return
            try:
                os.link(
                    temporary,
                    path.name,
                    src_dir_fd=parent,
                    dst_dir_fd=parent,
                    follow_symlinks=False,
                )
            except FileExistsError:
                raced = _read_private_file(parent, path.name, len(content))
                if raced != content:
                    raise OffloadSecurityError(reason="offload destination conflicts") from None
                return
            os.unlink(temporary, dir_fd=parent)
            os.fsync(parent)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=parent)
            fcntl.flock(parent, fcntl.LOCK_UN)
    finally:
        os.close(parent)


def read_scoped_regular_file(
    anchor: Path, relative: tuple[str, ...], *, max_bytes: int | None = None
) -> bytes:
    """Read a trusted relative file while pinning every directory component."""
    if not relative:
        raise OffloadSecurityError(reason="scoped file reference cannot be empty")
    parent = _open_scoped_directory(anchor, relative[:-1])
    if parent is None:
        raise OffloadSecurityError(reason="scoped file is unavailable")
    flags = nofollow_flags(os.O_RDONLY)
    try:
        try:
            descriptor = os.open(relative[-1], flags, dir_fd=parent)
        except FileNotFoundError as error:
            raise OffloadSecurityError(reason="scoped file is unavailable") from error
        except OSError as error:
            if error.errno in {ELOOP, ENOTDIR}:
                raise OffloadSecurityError(reason="scoped file must be a regular file") from error
            raise
        with os.fdopen(descriptor, "rb") as stream:
            _validate_private_regular_file(
                stream.fileno(), parent, relative[-1], "scoped file must be a private regular file"
            )
            if max_bytes is None:
                return stream.read()
            data = stream.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise OffloadSecurityError(reason="scoped file exceeds its recorded size")
            return data
    finally:
        os.close(parent)


def rollback_history_path(scope: OffloadMutationScope, actor_token: str, leaf: str) -> Path:
    """Locate the actor's mutable Markdown continuation under the conversation owner."""
    if not ROLLBACK_SESSION_FILE.fullmatch(leaf):
        raise OffloadSecurityError(reason="invalid rollback history session")
    return scope.internal_root.joinpath(
        "history",
        scope.owner,
        scope.conversation,
        "archives-v1",
        actor_token,
        "rollback",
        token("history-path", leaf),
    )


def private_file_exists(path: Path) -> bool:
    """Distinguish an absent file from a malformed or inaccessible scoped file."""
    parent = _open_scoped_directory(path.parent, ())
    if parent is None:
        return False
    try:
        try:
            descriptor = os.open(
                path.name, nofollow_flags(os.O_RDONLY | os.O_NONBLOCK), dir_fd=parent
            )
        except FileNotFoundError:
            return False
        try:
            _validate_private_regular_file(descriptor, parent, path.name, "invalid history file")
        finally:
            os.close(descriptor)
        return True
    finally:
        os.close(parent)


def imported_session_path(scope: OffloadMutationScope, legacy_owner_token: str, leaf: str) -> Path:
    return scope.internal_root.joinpath(
        "history", legacy_owner_token, scope.conversation, token("history-path", leaf)
    )


def resolve_rollback_session(
    scope: OffloadMutationScope, actor_token: str, leaf: str, *, legacy_owner_token: str | None
) -> Path:
    """An imported session can share the reserved name; prefer a published rollback."""
    current = rollback_history_path(scope, actor_token, leaf)
    if legacy_owner_token is None or private_file_exists(current):
        return current
    imported = imported_session_path(scope, legacy_owner_token, leaf)
    if private_file_exists(imported):
        check_conversation_offloads_active(scope)
        return imported
    return current


def read_rollback_history(
    data_dir: Path,
    *,
    owner_id: str,
    conversation_id: str,
    actor_id: UUID | str,
    leaf: str,
    legacy_owner_id: str | None = None,
) -> bytes:
    """Read a checkpoint-authorized rollback session without creating files."""
    scope = OffloadMutationScope(
        data_dir.resolve() / PHYSICAL_ROOT / "offload",
        token("owner", owner_id),
        token("conversation", conversation_id),
    )
    path = resolve_rollback_session(
        scope,
        token("actor", parse_actor(actor_id)),
        leaf,
        legacy_owner_token=token("owner", legacy_owner_id) if legacy_owner_id is not None else None,
    )
    return read_scoped_regular_file(path.parent, (path.name,))


def seed_rollback_history_locked(path: Path, pieces: Iterable[bytes]) -> None:
    """Atomically install Markdown or verify its original prefix after later appends.

    The caller holds the conversation mutation lock shared with edits and deletion.
    Readers only see a completed file. Checkpoint retries cannot truncate later turns.
    """
    parent = _open_scoped_directory(path.parent, ())
    if parent is None:
        raise OffloadSecurityError(reason="rollback history directory is unavailable")
    name = path.name
    temporary = f".rollback.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    try:
        try:
            existing = os.open(name, nofollow_flags(os.O_RDONLY | os.O_NONBLOCK), dir_fd=parent)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            with os.fdopen(existing, "rb") as stream:
                _validate_private_regular_file(
                    stream.fileno(), parent, name, "invalid rollback file"
                )
                for piece in pieces:
                    if stream.read(len(piece)) != piece:
                        raise OffloadSecurityError(reason="rollback history prefix conflicts")
            return
        flags = nofollow_flags(os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        descriptor = os.open(temporary, flags, 0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as stream:
            for piece in pieces:
                stream.write(piece)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        else:
            raise OffloadSecurityError(reason="rollback history changed during restoration")
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=parent)
        os.close(parent)
