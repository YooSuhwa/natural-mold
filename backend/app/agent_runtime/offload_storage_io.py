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


def _read_private_file(parent: int, name: str) -> bytes | None:
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
        return stream.read()


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
            existing = _read_private_file(parent, path.name)
            if existing is not None:
                if existing != content:
                    raise OffloadSecurityError(reason="offload destination conflicts")
                return
            descriptor = os.open(temporary, flags, 0o600, dir_fd=parent)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            raced = _read_private_file(parent, path.name)
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
                raced = _read_private_file(parent, path.name)
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


def read_scoped_regular_file(anchor: Path, relative: tuple[str, ...]) -> bytes:
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
            return stream.read()
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


def read_rollback_history(
    data_dir: Path, *, owner_id: str, conversation_id: str, actor_id: UUID | str, leaf: str
) -> bytes:
    """Read a checkpoint-authorized rollback session without creating files."""
    scope = OffloadMutationScope(
        data_dir.resolve() / PHYSICAL_ROOT / "offload",
        token("owner", owner_id),
        token("conversation", conversation_id),
    )
    path = rollback_history_path(scope, token("actor", parse_actor(actor_id)), leaf)
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
