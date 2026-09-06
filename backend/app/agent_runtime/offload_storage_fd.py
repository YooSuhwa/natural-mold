"""Fail-closed, dirfd-confined primitives for internal offload storage."""

from __future__ import annotations

import os
import stat
from contextlib import suppress
from errno import ELOOP, ENOTDIR
from pathlib import Path
from typing import Final, NoReturn

from app.agent_runtime.offload_storage_types import OffloadSecurityError

O_DIRECTORY: Final[int | None] = getattr(os, "O_DIRECTORY", None)
O_NOFOLLOW: Final[int | None] = getattr(os, "O_NOFOLLOW", None)
DIR_FD_CAPABILITIES: Final[bool] = (
    all(
        operation in os.supports_dir_fd
        for operation in (os.open, os.stat, os.mkdir, os.unlink, os.rmdir, os.rename, os.link)
    )
    and os.stat in os.supports_follow_symlinks
    and os.listdir in os.supports_fd
)


def require_secure_filesystem_capabilities() -> None:
    """Reject platforms that cannot enforce the internal no-follow boundary."""
    supported = (
        isinstance(O_DIRECTORY, int)
        and O_DIRECTORY != 0
        and isinstance(O_NOFOLLOW, int)
        and O_NOFOLLOW != 0
        and DIR_FD_CAPABILITIES
    )
    if not supported:
        raise OffloadSecurityError(reason="secure offload filesystem operations are unavailable")


def nofollow_flags(base: int) -> int:
    """Return flags that fail closed when no-follow is unavailable."""
    require_secure_filesystem_capabilities()
    nofollow = O_NOFOLLOW
    if not isinstance(nofollow, int) or nofollow == 0:
        raise OffloadSecurityError(reason="secure offload filesystem operations are unavailable")
    return base | nofollow


def directory_flags() -> int:
    """Return directory-open flags after checking the POSIX capability contract."""
    require_secure_filesystem_capabilities()
    directory = O_DIRECTORY
    nofollow = O_NOFOLLOW
    if not isinstance(directory, int) or not isinstance(nofollow, int):
        raise OffloadSecurityError(reason="secure offload filesystem operations are unavailable")
    return os.O_RDONLY | directory | nofollow


def raise_directory_error(error: OSError) -> NoReturn:
    """Translate directory link attacks to the public offload security failure."""
    if error.errno in {ELOOP, ENOTDIR}:
        raise OffloadSecurityError(reason="offload scope contains a symlink") from error
    raise error


def _absolute_parts(path: Path) -> tuple[str, ...]:
    absolute = path if path.is_absolute() else Path.cwd() / path
    if not absolute.anchor:
        raise OffloadSecurityError(reason="offload scope must be absolute")
    return absolute.parts[1:]


def open_directory_components(root: Path, relative: tuple[str, ...], *, create: bool) -> int | None:
    """Open a lexical absolute path one no-follow directory component at a time."""
    flags = directory_flags()
    absolute = root if root.is_absolute() else Path.cwd() / root
    descriptor = os.open(absolute.anchor, flags)
    try:
        for component in (*_absolute_parts(absolute), *relative):
            try:
                child = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    os.close(descriptor)
                    return None
                with suppress(FileExistsError):
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                child = _open_child_directory(component, descriptor)
            except OSError as error:
                os.close(descriptor)
                raise_directory_error(error)
            os.close(descriptor)
            descriptor = child
    except BaseException:
        with suppress(OSError):
            os.close(descriptor)
        raise
    return descriptor


def _open_child_directory(component: str, descriptor: int) -> int:
    try:
        return os.open(component, directory_flags(), dir_fd=descriptor)
    except OSError as error:
        raise_directory_error(error)


def safe_dir(root: Path, relative: tuple[str, ...]) -> Path:
    """Create one internal directory tree without following a raced symlink."""
    descriptor = open_directory_components(root, relative, create=True)
    if descriptor is None:
        raise OffloadSecurityError(reason="internal offload directory is unavailable")
    os.close(descriptor)
    return root.joinpath(*relative)


def ensure_private_root(root: Path) -> Path:
    """Create or open the internal root and require private owner-only access."""
    descriptor = open_directory_components(root, (), create=True)
    if descriptor is None:
        raise OffloadSecurityError(reason="internal offload root is unavailable")
    try:
        metadata = os.fstat(descriptor)
        valid = (
            stat.S_ISDIR(metadata.st_mode)
            and metadata.st_uid == os.geteuid()
            and stat.S_IMODE(metadata.st_mode) == 0o700
        )
        if not valid:
            raise OffloadSecurityError(reason="internal offload root must be private")
    finally:
        os.close(descriptor)
    return root


def open_scoped_directory(anchor: Path, relative: tuple[str, ...]) -> int | None:
    """Open an existing scope through every ancestor with O_NOFOLLOW."""
    return open_directory_components(anchor, relative, create=False)


def list_scoped_directories(anchor: Path, relative: tuple[str, ...]) -> tuple[str, ...]:
    """List direct non-link directory children of a pinned scope."""
    descriptor = open_scoped_directory(anchor, relative)
    if descriptor is None:
        return ()
    try:
        return tuple(
            sorted(
                name
                for name in os.listdir(descriptor)  # noqa: PTH208 -- dirfd pins trusted root
                if stat.S_ISDIR(os.stat(name, dir_fd=descriptor, follow_symlinks=False).st_mode)
            )
        )
    finally:
        os.close(descriptor)
