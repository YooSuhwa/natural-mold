"""Trusted toolchain and Git provenance for composite project gates."""

from __future__ import annotations

import os
import pwd
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from project_gate_runtime import (
    ProjectGateError,
    RuntimeDirectoryIdentity,
    capture_runtime_directory,
    normalized_version,
    run_trusted_process,
    runtime_version,
)

GIT: Final = Path("/usr/bin/git")
SYSTEM_PATH_CANDIDATES: Final = (
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
)
COMMIT_ID: Final = re.compile(r"^[0-9a-f]{40,64}$")
PYTHON_VERSION: Final = re.compile(r"Python (?P<version>3\.12\.\d+)")
NODE_VERSION: Final = re.compile(r"v(?P<version>22\.\d+\.\d+)")
PNPM_VERSION: Final = re.compile(r"(?P<version>\d+\.\d+\.\d+)")
NODE_RUNTIME_ENTRIES: Final = frozenset({"corepack", "node", "npm", "npx", "pnpm", "pnpx"})
PNPM_RUNTIME_ENTRIES: Final = frozenset({"pnpm", "pnpx"})


@dataclass(frozen=True, slots=True)
class ExecutableIdentity:
    """No-follow path and resolved target identity for one reviewed executable."""

    path: Path
    link_device: int
    link_inode: int
    target: Path
    target_device: int
    target_inode: int
    target_size: int
    target_mtime_ns: int


@dataclass(frozen=True, slots=True)
class TrustedToolchain:
    python: Path
    node: Path
    pnpm: Path
    home: Path
    user: str
    docker: Path = Path("/usr/local/bin/docker")
    system_paths: tuple[str, ...] = SYSTEM_PATH_CANDIDATES
    runtime_paths: tuple[str, ...] = ()
    identities: tuple[ExecutableIdentity, ...] = ()
    directory_identities: tuple[RuntimeDirectoryIdentity, ...] = ()

    @property
    def path(self) -> str:
        return os.pathsep.join((*self.runtime_paths, *self.system_paths))


@dataclass(frozen=True, slots=True)
class RepositoryProvenance:
    base_sha: str
    head_sha: str


def _run(command: list[str], repo_root: Path) -> subprocess.CompletedProcess[str]:
    return run_trusted_process(command, repo_root)


_capture_runtime_directory = capture_runtime_directory
_normalized_version = normalized_version
_version = runtime_version


def _first_executable(candidates: tuple[Path, ...]) -> Path:
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise ProjectGateError("runtime_preflight_failed")


def _one_hop_executable(path: Path) -> Path:
    if not path.is_symlink():
        return path
    try:
        target = path.readlink()
    except OSError as error:
        raise ProjectGateError("runtime_preflight_failed") from error
    if not target.is_absolute():
        target = path.parent / target
    # Lexically collapse ``..`` without resolving the target's second symlink hop.
    return Path(os.path.abspath(target))  # noqa: PTH100


def _trusted_system_paths() -> tuple[str, ...]:
    paths: list[str] = []
    for raw_path in SYSTEM_PATH_CANDIDATES:
        path = Path(raw_path)
        try:
            metadata = path.stat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ProjectGateError("runtime_preflight_failed") from error
        unsafe_mode = metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or unsafe_mode:
            raise ProjectGateError("runtime_preflight_failed")
        paths.append(raw_path)
    if "/usr/bin" not in paths or "/bin" not in paths:
        raise ProjectGateError("runtime_preflight_failed")
    return tuple(paths)


def _capture_executable(path: Path) -> ExecutableIdentity:
    try:
        link = path.lstat()
        target = path.resolve(strict=True)
        metadata = target.stat()
    except OSError as error:
        raise ProjectGateError("runtime_preflight_failed") from error
    valid_link = stat.S_ISREG(link.st_mode) or stat.S_ISLNK(link.st_mode)
    unsafe_mode = metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    if (
        not valid_link
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid not in {0, os.getuid()}
        or unsafe_mode
        or not os.access(target, os.X_OK)
    ):
        raise ProjectGateError("runtime_preflight_failed")
    return ExecutableIdentity(
        path,
        link.st_dev,
        link.st_ino,
        target,
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def verify_toolchain(toolchain: TrustedToolchain) -> None:
    """Fail closed when a reviewed executable path or target changes during a wave."""
    try:
        executable_changed = any(
            _capture_executable(expected.path) != expected for expected in toolchain.identities
        )
        directory_changed = any(
            _capture_runtime_directory(
                expected.path,
                required=expected.required,
                allowed=expected.allowed,
            )
            != expected
            for expected in toolchain.directory_identities
        )
    except ProjectGateError as error:
        raise ProjectGateError("runtime_changed") from error
    if executable_changed or directory_changed:
        raise ProjectGateError("runtime_changed")


def resolve_toolchain(repo_root: Path) -> tuple[TrustedToolchain, dict[str, str]]:
    """Resolve only account-owned or fixed-prefix executables and validate pinned majors."""
    account = pwd.getpwuid(os.getuid())
    home = Path(account.pw_dir)
    python = repo_root / "backend" / ".venv" / "bin" / "python"
    node_candidates = tuple(sorted((home / ".nvm/versions/node").glob("v22*/bin/node"))) + (
        Path("/opt/homebrew/opt/node@22/bin/node"),
        Path("/usr/local/opt/node@22/bin/node"),
    )
    node = _first_executable(node_candidates)
    pnpm = _one_hop_executable(
        _first_executable(
            (
                node.parent / "pnpm",
                home / "Library/pnpm/pnpm",
                home / ".local/share/pnpm/pnpm",
                Path("/opt/homebrew/bin/pnpm"),
                Path("/usr/local/bin/pnpm"),
            )
        )
    )
    system_paths = _trusted_system_paths()
    docker_name = shutil.which("docker", path=os.pathsep.join(system_paths))
    if docker_name is None:
        raise ProjectGateError("runtime_preflight_failed")
    docker = Path(docker_name)
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ProjectGateError("runtime_preflight_failed")
    runtime_specs = [(node.parent, "node", NODE_RUNTIME_ENTRIES)]
    if pnpm.parent != node.parent:
        runtime_specs.append((pnpm.parent, "pnpm", PNPM_RUNTIME_ENTRIES))
    directory_identities = tuple(
        _capture_runtime_directory(path, required=required, allowed=allowed)
        for path, required, allowed in runtime_specs
    )
    runtime_paths = tuple(str(identity.path) for identity in directory_identities)
    runtime_path = os.pathsep.join((*runtime_paths, *system_paths))
    python_version = _normalized_version(_version(python, repo_root), PYTHON_VERSION)
    node_version = _normalized_version(_version(node, repo_root), NODE_VERSION)
    # Homebrew's pnpm is a JavaScript entrypoint. Launch it with the reviewed
    # Node 22 binary instead of allowing its env shebang to select another Node.
    pnpm_version = _normalized_version(
        _version(
            pnpm,
            repo_root,
            launcher=node,
            path=runtime_path,
            disable_package_manager_delegation=True,
        ),
        PNPM_VERSION,
    )
    identities = tuple(_capture_executable(path) for path in (python, node, pnpm, docker))
    toolchain = TrustedToolchain(
        python,
        node,
        pnpm,
        home,
        account.pw_name,
        docker,
        system_paths,
        runtime_paths,
        identities,
        directory_identities,
    )
    verify_toolchain(toolchain)
    return toolchain, {"python": python_version, "node": node_version, "pnpm": pnpm_version}


def resolve_provenance(base: str, repo_root: Path) -> RepositoryProvenance:
    """Resolve full commit identities and require a clean tracked/untracked worktree."""
    base_result = _run([str(GIT), "rev-parse", "--verify", f"{base}^{{commit}}"], repo_root)
    head_result = _run([str(GIT), "rev-parse", "--verify", "HEAD^{commit}"], repo_root)
    base_sha = base_result.stdout.strip()
    head_sha = head_result.stdout.strip()
    if (
        base_result.returncode != 0
        or head_result.returncode != 0
        or not COMMIT_ID.fullmatch(base_sha)
        or not COMMIT_ID.fullmatch(head_sha)
    ):
        raise ProjectGateError("base_not_commit")
    ancestor = _run([str(GIT), "merge-base", "--is-ancestor", base_sha, head_sha], repo_root)
    if ancestor.returncode != 0:
        raise ProjectGateError("base_not_ancestor")
    provenance = RepositoryProvenance(base_sha, head_sha)
    verify_provenance(provenance, repo_root)
    return provenance


def verify_provenance(expected: RepositoryProvenance, repo_root: Path) -> None:
    """Fail when HEAD or any non-ignored worktree entry changes during a wave."""
    head = _run([str(GIT), "rev-parse", "--verify", "HEAD^{commit}"], repo_root)
    status = _run(
        [str(GIT), "status", "--porcelain=v1", "--untracked-files=all"],
        repo_root,
    )
    if (
        head.returncode != 0
        or head.stdout.strip() != expected.head_sha
        or status.returncode != 0
        or bool(status.stdout)
    ):
        raise ProjectGateError("repository_changed")
