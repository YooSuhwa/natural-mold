"""Persistent conversation mutation lock and deletion fence."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

try:
    import fcntl as _fcntl_module
except ModuleNotFoundError:  # pragma: no cover - exercised through the capability seam
    _fcntl_module = None

from app.agent_runtime.offload_storage_fd import (
    ensure_private_root,
    nofollow_flags,
    open_directory_components,
    raise_directory_error,
    require_secure_filesystem_capabilities,
)
from app.agent_runtime.offload_storage_types import OffloadMutationScope, OffloadSecurityError


class _FcntlApi(Protocol):
    @property
    def LOCK_EX(self) -> int: ...  # noqa: N802 -- mirrors the POSIX module constant

    @property
    def LOCK_UN(self) -> int: ...  # noqa: N802 -- mirrors the POSIX module constant

    def flock(self, descriptor: int, operation: int, /) -> None: ...


_FCNTL: _FcntlApi | None = _fcntl_module


@dataclass(frozen=True, slots=True)
class _ControlIdentity:
    device: int
    inode: int


def _identity(metadata: os.stat_result) -> _ControlIdentity:
    return _ControlIdentity(device=metadata.st_dev, inode=metadata.st_ino)


def _scope_digest(scope: OffloadMutationScope) -> str:
    return hashlib.sha256(f"{scope.owner}\0{scope.conversation}".encode()).hexdigest()


def _validate_control_file(
    descriptor: int,
    parent: int,
    name: str,
    *,
    invalid_reason: str,
    changed_reason: str,
) -> None:
    opened = os.fstat(descriptor)
    try:
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError as error:
        raise OffloadSecurityError(reason=changed_reason) from error
    valid = (
        stat.S_ISREG(opened.st_mode)
        and opened.st_nlink == 1
        and stat.S_ISREG(current.st_mode)
        and current.st_nlink == 1
        and _identity(opened) == _identity(current)
    )
    if not valid:
        raise OffloadSecurityError(reason=invalid_reason)


def _reject_deleted_conversation(lock_root: int, scope: OffloadMutationScope) -> None:
    name = f"{_scope_digest(scope)}.deleted"
    try:
        descriptor = os.open(name, nofollow_flags(os.O_RDONLY), dir_fd=lock_root)
    except FileNotFoundError:
        return
    except OSError as error:
        raise_directory_error(error)
    try:
        _validate_control_file(
            descriptor,
            lock_root,
            name,
            invalid_reason="offload deletion fence is invalid",
            changed_reason="offload deletion fence changed",
        )
    finally:
        os.close(descriptor)
    raise OffloadSecurityError(reason="conversation offload storage was deleted")


def _mark_conversation_deleted(lock_root: int, scope: OffloadMutationScope) -> None:
    name = f"{_scope_digest(scope)}.deleted"
    flags = nofollow_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    created = False
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=lock_root)
        created = True
    except FileExistsError:
        try:
            descriptor = os.open(name, nofollow_flags(os.O_RDONLY), dir_fd=lock_root)
        except OSError as error:
            raise_directory_error(error)
    except OSError as error:
        raise_directory_error(error)
    try:
        _validate_control_file(
            descriptor,
            lock_root,
            name,
            invalid_reason="offload deletion fence is invalid",
            changed_reason="offload deletion fence changed",
        )
        if created:
            os.fsync(descriptor)
            os.fsync(lock_root)
    finally:
        os.close(descriptor)


@contextmanager
def _conversation_control_lock(
    scope: OffloadMutationScope, *, allow_deleted: bool
) -> Iterator[int]:
    fcntl_api = _FCNTL
    if fcntl_api is None:
        raise OffloadSecurityError(reason="secure offload filesystem operations are unavailable")
    require_secure_filesystem_capabilities()
    private_root = ensure_private_root(scope.internal_root.parent)
    lock_root = open_directory_components(private_root, ("gc-lock",), create=True)
    if lock_root is None:
        raise OffloadSecurityError(reason="offload mutation lock is unavailable")
    name = f"{_scope_digest(scope)}.lock"
    flags = nofollow_flags(os.O_RDWR | os.O_CREAT)
    descriptor: int | None = None
    try:
        fcntl_api.flock(lock_root, fcntl_api.LOCK_EX)
        try:
            try:
                descriptor = os.open(name, flags, 0o600, dir_fd=lock_root)
                _validate_control_file(
                    descriptor,
                    lock_root,
                    name,
                    invalid_reason="offload cleanup lock is invalid",
                    changed_reason="offload cleanup lock changed",
                )
            except OSError as error:
                raise_directory_error(error)
        finally:
            fcntl_api.flock(lock_root, fcntl_api.LOCK_UN)
        fcntl_api.flock(descriptor, fcntl_api.LOCK_EX)
        try:
            _validate_control_file(
                descriptor,
                lock_root,
                name,
                invalid_reason="offload cleanup lock is invalid",
                changed_reason="offload cleanup lock changed",
            )
            if not allow_deleted:
                _reject_deleted_conversation(lock_root, scope)
            yield lock_root
        finally:
            fcntl_api.flock(descriptor, fcntl_api.LOCK_UN)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(lock_root)


@contextmanager
def conversation_mutation_lock(scope: OffloadMutationScope) -> Iterator[None]:
    """Serialize a mutation and reject conversations fenced for deletion."""
    with _conversation_control_lock(scope, allow_deleted=False):
        yield


@contextmanager
def conversation_deletion_lock(scope: OffloadMutationScope) -> Iterator[None]:
    """Fence future mutations before deleting one conversation's offloads."""
    with _conversation_control_lock(scope, allow_deleted=True) as lock_root:
        _mark_conversation_deleted(lock_root, scope)
        yield
