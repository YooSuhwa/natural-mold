"""Trusted Docker executable resolution for independent cleanup validators."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Never, Protocol

SYSTEM_PATH_CANDIDATES: Final = (
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
)
MANDATORY_SYSTEM_PATHS: Final = frozenset({"/usr/bin", "/bin"})
DOCKER_ENVIRONMENT_NAME: Final = "MOLDY_GATE_DOCKER"
DOCKER_IDENTITY_ENVIRONMENT_NAME: Final = "MOLDY_GATE_DOCKER_IDENTITY"
IDENTITY_TOKEN: Final = re.compile(r"^[0-9a-f]{64}$")
MAX_DOCKER_EXECUTABLE_BYTES: Final = 512 * 1024 * 1024
READ_CHUNK_BYTES: Final = 1024 * 1024


class ExecutableIdentityLike(Protocol):
    """Structural identity shared with the project-gate preflight."""

    @property
    def path(self) -> Path: ...

    @property
    def link_device(self) -> int: ...

    @property
    def link_inode(self) -> int: ...

    @property
    def target(self) -> Path: ...

    @property
    def target_device(self) -> int: ...

    @property
    def target_inode(self) -> int: ...

    @property
    def target_size(self) -> int: ...

    @property
    def target_mtime_ns(self) -> int: ...

    @property
    def target_ctime_ns(self) -> int: ...

    @property
    def target_sha256(self) -> str: ...

    @property
    def require_regular(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class DockerExecutableIdentity:
    """No-follow path and resolved target identity for Docker."""

    path: Path
    link_device: int
    link_inode: int
    target: Path
    target_device: int
    target_inode: int
    target_size: int
    target_mtime_ns: int
    target_ctime_ns: int
    target_sha256: str
    require_regular: bool = False


class DockerTrustError(OSError):
    """Stable failure for an unavailable or untrusted Docker executable."""


def _reject(error: OSError | None = None) -> Never:
    rejection = DockerTrustError("docker_preflight_failed")
    if error is None:
        raise rejection
    raise rejection from error


def _trusted_system_paths() -> tuple[str, ...]:
    paths: list[str] = []
    for raw_path in SYSTEM_PATH_CANDIDATES:
        path = Path(raw_path)
        try:
            metadata = path.stat()
        except FileNotFoundError:
            continue
        except OSError as error:
            _reject(error)
        is_unsafe_directory = (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        )
        if is_unsafe_directory:
            if raw_path in MANDATORY_SYSTEM_PATHS:
                _reject()
            continue
        paths.append(raw_path)
    if not MANDATORY_SYSTEM_PATHS.issubset(paths):
        _reject()
    return tuple(paths)


def _require_target_outside_omitted_system_paths(
    target: Path, system_paths: tuple[str, ...]
) -> None:
    omitted_roots = tuple(
        Path(raw_path) for raw_path in SYSTEM_PATH_CANDIDATES if raw_path not in system_paths
    )
    if any(target.is_relative_to(root) for root in omitted_roots):
        _reject()


def _capture_target_contents(target: Path) -> tuple[os.stat_result, str]:
    """Read one regular executable through a no-follow descriptor and bind its bytes."""
    try:
        descriptor = os.open(target, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as error:
        _reject(error)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size < 0
            or before.st_size > MAX_DOCKER_EXECUTABLE_BYTES
        ):
            _reject()
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, READ_CHUNK_BYTES):
            digest.update(chunk)
        after = os.fstat(descriptor)
    except OSError as error:
        _reject(error)
    finally:
        os.close(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        _reject()
    return before, digest.hexdigest()


def capture_docker_identity(path: Path) -> DockerExecutableIdentity:
    """Capture the executable identity used by the project-gate preflight."""
    try:
        link = path.lstat()
        target = path.resolve(strict=True)
    except OSError as error:
        _reject(error)
    metadata, target_sha256 = _capture_target_contents(target)
    if (
        not (stat.S_ISREG(link.st_mode) or stat.S_ISLNK(link.st_mode))
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in {0, os.getuid()}
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or not os.access(target, os.X_OK)
    ):
        _reject()
    return DockerExecutableIdentity(
        path,
        link.st_dev,
        link.st_ino,
        target,
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        target_sha256,
    )


def docker_identity_token(identity: ExecutableIdentityLike) -> str:
    """Return a canonical digest for every preflight identity field."""
    payload = json.dumps(
        [
            str(identity.path),
            identity.link_device,
            identity.link_inode,
            str(identity.target),
            identity.target_device,
            identity.target_inode,
            identity.target_size,
            identity.target_mtime_ns,
            identity.target_ctime_ns,
            identity.target_sha256,
            identity.require_regular,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _verify_expected_identity(identity: DockerExecutableIdentity) -> None:
    expected = os.environ.get(DOCKER_IDENTITY_ENVIRONMENT_NAME)
    if expected is None:
        return
    current = docker_identity_token(identity)
    if not IDENTITY_TOKEN.fullmatch(expected) or not hmac.compare_digest(expected, current):
        raise DockerTrustError("docker_identity_changed")


def verify_docker_identity(path: Path) -> None:
    """Revalidate the selected executable against an optional preflight token."""
    _verify_expected_identity(capture_docker_identity(path))


def docker_subprocess_environment(
    inherited: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build an endpoint-neutral Docker environment with one explicit bootstrap secret."""
    environment = {
        "HOME": "/",
        "LC_ALL": "C",
        "PATH": os.pathsep.join(_trusted_system_paths()),
    }
    if inherited is not None and "POSTGRES_PASSWORD" in inherited:
        environment["POSTGRES_PASSWORD"] = inherited["POSTGRES_PASSWORD"]
    return environment


def resolve_trusted_docker() -> str:
    """Return Docker from fixed reviewed prefixes, never inherited PATH."""
    system_paths = _trusted_system_paths()
    requested = os.environ.get(DOCKER_ENVIRONMENT_NAME)
    selected = requested or shutil.which("docker", path=os.pathsep.join(system_paths))
    if selected is None:
        _reject()
    docker = Path(selected)
    if (
        not docker.is_absolute()
        or docker.name != "docker"
        or str(docker.parent) not in system_paths
    ):
        _reject()
    identity = capture_docker_identity(docker)
    _require_target_outside_omitted_system_paths(identity.target, system_paths)
    return str(docker)


def probe_docker(arguments: tuple[str, ...], *, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run one read-only Docker probe without caller-selected daemon state."""
    docker = resolve_trusted_docker()
    environment = docker_subprocess_environment()
    command = (docker, *arguments)
    verify_docker_identity(Path(docker))
    return subprocess.run(  # noqa: S603 - absolute trusted executable and internal argv
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=environment,
    )
