"""Process boundary for a configured project gate."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NamedTuple

type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]


class ProjectGateError(RuntimeError):
    """Return stable, secret-free failure identifiers at the command boundary."""


class GateProfile(NamedTuple):
    lane: str
    project: str
    spec: str
    workers: int
    retries: int


class CompositeProfile(NamedTuple):
    nodes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuntimeDirectoryIdentity:
    """Stable identity and exact allowlist for one directory exposed through PATH."""

    path: Path
    device: int
    inode: int
    owner: int
    mode: int
    entries: tuple[str, ...]
    required: str
    allowed: frozenset[str]


def capture_runtime_directory(
    path: Path, *, required: str, allowed: frozenset[str]
) -> RuntimeDirectoryIdentity:
    """Capture a narrow account/root-owned runtime directory without following links."""
    try:
        metadata = path.lstat()
        entries = tuple(sorted(entry.name for entry in os.scandir(path)))
    except OSError as error:
        raise ProjectGateError("runtime_preflight_failed") from error
    unsafe_mode = metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid not in {0, os.getuid()}
        or unsafe_mode
        or required not in entries
        or not set(entries) <= allowed
    ):
        raise ProjectGateError("runtime_preflight_failed")
    return RuntimeDirectoryIdentity(
        path,
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        stat.S_IMODE(metadata.st_mode),
        entries,
        required,
        allowed,
    )


def run_trusted_process(
    command: list[str],
    repo_root: Path,
    *,
    path: str = "/usr/bin:/bin:/usr/sbin:/sbin",
    disable_package_manager_delegation: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run an absolute executable with a fixed, secret-free environment."""
    environment = {"PATH": path, "LC_ALL": "C"}
    if disable_package_manager_delegation:
        environment["npm_config_manage_package_manager_versions"] = "false"
    try:
        return subprocess.run(  # noqa: S603 - absolute reviewed executable
            command,
            cwd=repo_root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise ProjectGateError("trusted_tool_start_failed") from error


def runtime_version(
    executable: Path,
    repo_root: Path,
    *,
    launcher: Path | None = None,
    path: str = "/usr/bin:/bin:/usr/sbin:/sbin",
    disable_package_manager_delegation: bool = False,
) -> str:
    """Probe a runtime without returning child errors at the trust boundary."""
    command = [str(executable), "--version"]
    if launcher is not None:
        command = [str(launcher), str(executable), "--version"]
    result = run_trusted_process(
        command,
        repo_root,
        path=path,
        disable_package_manager_delegation=disable_package_manager_delegation,
    )
    if result.returncode != 0:
        raise ProjectGateError("runtime_preflight_failed")
    return (result.stdout or result.stderr).strip()


def normalized_version(output: str, pattern: re.Pattern[str]) -> str:
    """Accept an exact version grammar and retain only its numeric token."""
    matched = pattern.fullmatch(output)
    if matched is None:
        raise ProjectGateError("runtime_preflight_failed")
    return matched.group("version")


LAUNCHER_ENVIRONMENT_NAMES: Final[frozenset[str]] = frozenset(
    {
        "PATH",
        "HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "TZ",
        "USER",
        "LOGNAME",
        "PNPM_HOME",
        "CI",
        "DOCKER_HOST",
        "DOCKER_CONFIG",
        "DOCKER_CONTEXT",
    }
)
LIVE_LLM_ENVIRONMENT_NAMES: Final[frozenset[str]] = frozenset(
    {"E2E_LLM_BASE_URL", "E2E_LLM_API_KEY", "E2E_LLM_MODEL"}
)
OWNED_LOOPBACK_OPT_IN: Final = "E2E_EGRESS_ALLOW_OWNED_LOOPBACK"
CHECKER_ENVIRONMENT_NAMES: Final[frozenset[str]] = LAUNCHER_ENVIRONMENT_NAMES


def _run_cleanup_checker(
    manifest: Path, repo_root: Path, inherited_environment: dict[str, str]
) -> bool:
    """Run the fixed-path cleanup checker without reflecting its output."""
    checker_python = repo_root / "backend" / ".venv" / "bin" / "python"
    if not checker_python.is_file() or not os.access(checker_python, os.X_OK):
        checker_python = Path(sys.executable)
    checker_environment = {
        key: value
        for key, value in inherited_environment.items()
        if key in CHECKER_ENVIRONMENT_NAMES
    }
    command = [
        str(checker_python),
        str(repo_root / "scripts" / "check-isolation-cleanup.py"),
        str(manifest),
    ]
    try:
        checker = subprocess.run(  # noqa: S603 - fixed local checker with validated manifest path
            command,
            cwd=repo_root,
            env=checker_environment,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    if checker.returncode != 0:
        return False
    try:
        metadata = manifest.lstat()
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)


def run_profile_with_environment(
    profile_name: str,
    profile: GateProfile,
    base_sha: str,
    manifest: Path,
    repo_root: Path,
    inherited_environment: dict[str, str],
) -> int:
    """Run the lifecycle child and validate its manifest only after child success."""
    if profile.workers != 1 or profile.retries != 0:
        raise ProjectGateError("invalid_config")
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise ProjectGateError("pnpm_start_failed")
    environment = {
        key: value
        for key, value in inherited_environment.items()
        if key in LAUNCHER_ENVIRONMENT_NAMES
    }
    if profile.lane == "live":
        environment.update(
            {
                key: inherited_environment[key]
                for key in LIVE_LLM_ENVIRONMENT_NAMES
                if key in inherited_environment
            }
        )
        if inherited_environment.get(OWNED_LOOPBACK_OPT_IN) == "1":
            environment[OWNED_LOOPBACK_OPT_IN] = "1"
    environment.update(
        {
            "E2E_RUN_MANIFEST": str(manifest),
            "E2E_EXPORT_SLUG": (
                f"project-gate-{profile_name}-{base_sha[:12]}-{secrets.token_hex(8)}"
            ),
        }
    )
    command = [
        pnpm,
        "--dir",
        "frontend",
        f"test:e2e:{profile.lane}",
        "--",
        f"--project={profile.project}",
        profile.spec,
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed pnpm argv built from validated profile
            command, cwd=repo_root, env=environment, check=False
        )
    except OSError as error:
        raise ProjectGateError("pnpm_start_failed") from error
    if completed.returncode != 0:
        return completed.returncode
    return 0 if _run_cleanup_checker(manifest, repo_root, inherited_environment) else 1
