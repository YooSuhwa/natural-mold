"""Read-only CLI for validating PostgreSQL and E2E lifecycle evidence."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final, Literal

from cleanup_discovery import DiscoverySummary, discover_claims
from cleanup_discovery_claims import Claims
from cleanup_docker import probe_docker
from e2e_cleanup_checker import validate_live_absence as validate_e2e_live_absence
from e2e_cleanup_checker import validate_payload as validate_e2e_payload
from e2e_cleanup_contract import PORTS, require
from e2e_cleanup_export_paths import _PinnedDirectory
from postgres_cleanup_checker import (
    ManifestValidationError,
    load_manifest,
    validate_live_absence,
    validate_payload,
)
from postgres_runner_runtime import OWNER_LABEL

SYSTEM_TEMP: Path = Path(tempfile.gettempdir()).resolve(strict=True)
LEGACY_TEMP_PARENTS: tuple[Path, ...] = tuple(
    dict.fromkeys(
        path.resolve(strict=True)
        for path in (Path("/").joinpath("tmp"), Path("/").joinpath("private", "tmp"))
        if path.exists()
    )
)
_ROOT_PREFIXES: Final = (
    ".moldy-test-run.",
    ".moldy-pg-run-",
    ".moldy-test-quarantine.",
)
_WRAPPER_ROOT: Final = re.compile(r"\.moldy-test-run\.[A-Za-z0-9]{8}")
_MAX_TEMP_ENTRIES: Final = 32_768
_MAX_EXTERNAL_PROBES: Final = 768
_PROBE_DEADLINE_SECONDS: Final = 30.0
_DOCKER_PROBE_SECONDS: Final = 10.0
_PROCESS_PROBE_SECONDS: Final = 2.0
REPO_ROOT: Final = Path(__file__).resolve().parents[1]
type RootEntryIdentity = tuple[str, int, int]
_WRAPPER_COMMAND_PREFIX: Final = (
    "/bin/bash",
    str(REPO_ROOT / "scripts" / "run-isolated-command.sh"),
    "--cwd",
    "backend",
    "--",
)


def _run_probe(argv: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed internal argv, never a shell command
        argv, capture_output=True, text=True, timeout=timeout, check=False
    )


def _port_has_listener(port: int) -> bool:
    for family, host in (
        (socket.AF_INET, "127.0.0.1"),
        (socket.AF_INET6, "::1"),
    ):
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.25)
            if probe.connect_ex((host, port)) == 0:
                return True
    return False


def _temp_parents() -> tuple[Path, ...]:
    return tuple(dict.fromkeys((SYSTEM_TEMP, *LEGACY_TEMP_PARENTS)))


def _active_identity(raw: str | None) -> tuple[int, int] | None:
    if raw is None:
        return None
    path = Path(raw)
    try:
        metadata = path.lstat()
        parent = path.parent.resolve(strict=True)
    except OSError as error:
        raise ManifestValidationError("active_run_root") from error
    require(
        path.is_absolute()
        and parent == SYSTEM_TEMP
        and _WRAPPER_ROOT.fullmatch(path.name) is not None
        and stat.S_ISDIR(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and metadata.st_mode & 0o077 == 0,
        "active_run_root",
    )
    return metadata.st_dev, metadata.st_ino


def _probe_timeout(deadline: float, limit: float) -> float:
    remaining = deadline - time.monotonic()
    require(remaining > 0, "probe_error")
    return min(remaining, limit)


def _probe_processes(claims: Claims, deadline: float) -> None:
    label = probe_docker(
        ("ps", "-aq", "--filter", f"label={OWNER_LABEL}"),
        timeout=_probe_timeout(deadline, _DOCKER_PROBE_SECONDS),
    )
    require(label.returncode == 0, "probe_error")
    require(not label.stdout.strip(), "live_label")
    for process_id, expected_hash in sorted(claims.processes):
        result = _run_probe(
            ("/bin/ps", "-p", str(process_id), "-o", "lstart=,command="),
            _probe_timeout(deadline, _PROCESS_PROBE_SECONDS),
        )
        if result.returncode == 1:
            continue
        require(result.returncode == 0 and bool(result.stdout), "probe_error")
        require(
            hashlib.sha256(result.stdout.encode()).hexdigest() != expected_hash,
            "live_process",
        )


def _probe_roots(claims: Claims, active: tuple[int, int] | None) -> None:
    recorded = {str(root) for root in claims.run_roots}
    examined = 0
    for parent in _temp_parents():
        with _PinnedDirectory(parent) as pinned:

            def relevant(name: str, scope: Path = parent) -> bool:
                return str(scope / name) in recorded or name.startswith(_ROOT_PREFIXES)

            def relevant_entries() -> tuple[RootEntryIdentity, ...]:
                nonlocal examined
                identities: list[RootEntryIdentity] = []
                with os.scandir(pinned.fd) as entries:
                    for entry in entries:
                        examined += 1
                        require(examined <= _MAX_TEMP_ENTRIES, "probe_error")
                        if relevant(entry.name):
                            metadata = os.stat(entry.name, dir_fd=pinned.fd, follow_symlinks=False)
                            identities.append((entry.name, metadata.st_dev, metadata.st_ino))
                return tuple(sorted(identities))

            before = relevant_entries()
            for _, device, inode in before:
                require(active == (device, inode), "live_run_root")
            pinned.validate("probe_error", require_unchanged=False)
            after = relevant_entries()
            require(before == after, "probe_error")


def _active_from_environment() -> str | None:
    raw = os.environ.get("MOLDY_TEST_RUN_ROOT")
    if raw is None:
        return None
    require(
        os.environ.get("MOLDY_BACKEND_SOURCE_ROOT") == str(REPO_ROOT / "backend")
        and os.environ.get("MOLDY_DISABLE_ENV_FILE") == "true"
        and os.environ.get("PYTHON_DOTENV_DISABLED") == "1",
        "active_run_root",
    )
    try:
        parent = _run_probe(("/bin/ps", "-ww", "-p", str(os.getppid()), "-o", "command="), 5)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ManifestValidationError("active_run_root") from error
    command = tuple(parent.stdout.strip().split())
    require(
        parent.returncode == 0
        and command[: len(_WRAPPER_COMMAND_PREFIX)] == _WRAPPER_COMMAND_PREFIX,
        "active_run_root",
    )
    return raw


def discover_and_probe(
    root: Path,
    *,
    active_run_root: str | None = None,
    after_read: Callable[[], None] | None = None,
) -> DiscoverySummary:
    """Discover lifecycle claims and independently prove no live residue."""
    claims, summary = discover_claims(
        root,
        temp_parents=_temp_parents(),
        after_read=after_read,
    )
    try:
        ports = {port for lane_ports in PORTS.values() for port in lane_ports} | set(claims.ports)
        workload = 1 + len(claims.processes) + 2 * len(ports)
        require(workload <= _MAX_EXTERNAL_PROBES, "probe_error")
        deadline = time.monotonic() + _PROBE_DEADLINE_SECONDS
        _probe_processes(claims, deadline)
        _probe_roots(claims, _active_identity(active_run_root))
        for port in sorted(ports):
            require(time.monotonic() < deadline, "probe_error")
            require(not _port_has_listener(port), "live_port")
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ManifestValidationError("probe_error") from error
    return summary


type ArtifactScope = Literal["manifest-only", "full"]


def _validate_manifest(manifest: Path) -> ArtifactScope:
    """Dispatch only after the shared no-follow manifest read boundary parsed the runner."""
    payload = load_manifest(manifest)
    if payload.get("runner") == "moldy-isolated-e2e" and payload.get("schema_version") == 1:
        scope = validate_e2e_payload(payload, receipt_path=manifest)
        validate_e2e_live_absence(payload)
        return scope
    validate_payload(payload)
    validate_live_absence(payload)
    return "full"


def main() -> int:
    parser = argparse.ArgumentParser()
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("manifests", nargs="*", type=Path)
    sources.add_argument("--discover", type=Path)
    parser.add_argument("--print-artifact-scope", action="store_true")
    args = parser.parse_args()
    if args.discover is not None:
        try:
            summary = discover_and_probe(
                args.discover,
                active_run_root=_active_from_environment(),
            )
        except ManifestValidationError as error:
            print(f"cleanup discovery rejected: {error}", file=sys.stderr)
            return 1
        print(f"discovered={len(summary.receipts)}")
        return 0
    if not args.manifests:
        parser.error("one or more manifests are required")
    if args.print_artifact_scope and len(args.manifests) != 1:
        parser.error("--print-artifact-scope requires exactly one manifest")
    scopes = [_validate_manifest(manifest) for manifest in args.manifests]
    if args.print_artifact_scope:
        print(scopes[0])
        return 0
    print(f"validated={len(args.manifests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
