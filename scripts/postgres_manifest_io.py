"""Trusted manifest I/O and lifecycle receipt helpers."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from final_attempt_authority import (
    FinalAttemptAuthorityError,
    verify_open_final_attempt_authority,
)
from postgres_runner_contract import ensure_evidence_root, validate_manifest_destination
from postgres_runner_runtime import (
    REPO_ROOT,
    ExternalScenarioKind,
    ScenarioKind,
    process_identity_sha256,
)

_CLEANUP_FIELDS: Final = (
    "cleanup_container_removed",
    "owned_label_absent",
    "port_mapping_removed",
    "process_group_stopped",
    "cleanup_run_root_removed",
)
_SIGNALS: Final = (signal.SIGINT, signal.SIGTERM)
EVIDENCE_ROOT: Final = REPO_ROOT / ".omo/evidence/project-restart-consolidated-roadmap"


class ManifestPathError(RuntimeError):
    """Stable error raised when a manifest path loses its trusted identity."""


@dataclass(frozen=True, slots=True)
class RunnerInterrupted(BaseException):
    signal_number: int


def on_signal(signal_number: int, _frame: object) -> None:
    raise RunnerInterrupted(signal_number)


@dataclass(frozen=True, slots=True)
class EvidenceDirectory:
    path: Path
    descriptor: int
    device: int
    inode: int
    root_descriptor: int | None = None
    attempt_id: str | None = None
    head_sha: str | None = None
    root_path: Path | None = None
    root_device: int | None = None
    root_inode: int | None = None


class ScenarioRunner(Protocol):
    def __call__(
        self, kind: ScenarioKind, *, process_id: int, process_identity: str
    ) -> dict[str, object]: ...


class EarlyInterruptBuilder(Protocol):
    def __call__(
        self,
        signal_number: int,
        *,
        process_id: int,
        process_identity: str,
        mode: ExternalScenarioKind,
    ) -> dict[str, object]: ...


def open_evidence_directory(root: Path) -> EvidenceDirectory:
    path = root.absolute()
    expected = path.stat(follow_symlinks=False)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ManifestPathError("evidence_identity") from error
    actual = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(expected.st_mode)
        or not stat.S_ISDIR(actual.st_mode)
        or actual.st_uid != os.geteuid()
        or actual.st_mode & 0o022
        or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)
    ):
        os.close(descriptor)
        raise ManifestPathError("evidence_identity")
    return EvidenceDirectory(path, descriptor, actual.st_dev, actual.st_ino)


def _read_bound_pointer(root_descriptor: int) -> dict[str, object]:
    try:
        descriptor = os.open(
            "current-final-attempt.json",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=root_descriptor,
        )
    except OSError as error:
        raise ManifestPathError("final_pointer") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o022
            or metadata.st_size > 64 * 1024
        ):
            raise ManifestPathError("final_pointer")
        payload = os.read(descriptor, metadata.st_size + 1)
        named = os.stat(
            "current-final-attempt.json",
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
        if (named.st_dev, named.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ManifestPathError("final_pointer")
    finally:
        os.close(descriptor)
    try:
        decoded = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ManifestPathError("final_pointer") from error
    if not isinstance(decoded, dict):
        raise ManifestPathError("final_pointer")
    return decoded


def _current_git_head(repo_root: Path) -> str:
    git = shutil.which("git")
    if git is None:
        raise ManifestPathError("final_head")
    try:
        result = subprocess.run(  # noqa: S603 - fixed read-only Git query
            [git, "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise ManifestPathError("final_head") from error
    head = result.stdout.strip()
    if result.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise ManifestPathError("final_head")
    return head


def open_manifest_directory(
    destination: Path, evidence_root: Path, repo_root: Path
) -> EvidenceDirectory:
    """Bind either the ordinary evidence root or its current open final-attempt directory."""
    root = open_evidence_directory(evidence_root)
    absolute = destination.absolute()
    if absolute.parent == root.path:
        try:
            os.stat(absolute.name, dir_fd=root.descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return root
        close_evidence_directory(root)
        raise FileExistsError(absolute.name)
    expected_parent = root.path / "final-attempts"
    if absolute.parent.parent != expected_parent:
        close_evidence_directory(root)
        raise ManifestPathError("manifest_boundary")
    attempt_id = absolute.parent.name
    if re.fullmatch(r"[0-9a-f]{64}", attempt_id) is None:
        close_evidence_directory(root)
        raise ManifestPathError("manifest_boundary")
    try:
        final_root = os.open(
            "final-attempts",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=root.descriptor,
        )
        final_root_metadata = os.fstat(final_root)
        if (
            not stat.S_ISDIR(final_root_metadata.st_mode)
            or final_root_metadata.st_uid != os.geteuid()
            or final_root_metadata.st_mode & 0o022
        ):
            raise ManifestPathError("final_attempt_directory")
        descriptor = os.open(
            attempt_id,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=final_root,
        )
    except OSError as error:
        close_evidence_directory(root)
        raise ManifestPathError("final_attempt_directory") from error
    finally:
        if "final_root" in locals():
            os.close(final_root)
    metadata = os.fstat(descriptor)
    head = _current_git_head(repo_root)
    try:
        verify_open_final_attempt_authority(
            evidence_root=root.path,
            evidence_root_fd=root.descriptor,
            expected_attempt_id=attempt_id,
            expected_attempt_path=absolute.parent,
            expected_head=head,
        )
    except FinalAttemptAuthorityError as error:
        os.close(descriptor)
        close_evidence_directory(root)
        raise ManifestPathError("final_attempt_binding") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_mode & 0o022
    ):
        os.close(descriptor)
        close_evidence_directory(root)
        raise ManifestPathError("final_attempt_binding")
    try:
        os.stat(absolute.name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return EvidenceDirectory(
            absolute.parent,
            descriptor,
            metadata.st_dev,
            metadata.st_ino,
            root.descriptor,
            attempt_id,
            head,
            root.path,
            root.device,
            root.inode,
        )
    os.close(descriptor)
    close_evidence_directory(root)
    raise FileExistsError(absolute.name)


def close_evidence_directory(evidence: EvidenceDirectory) -> None:
    os.close(evidence.descriptor)
    if evidence.root_descriptor is not None:
        os.close(evidence.root_descriptor)


def _verify_evidence_directory(evidence: EvidenceDirectory) -> None:
    try:
        current = evidence.path.stat(follow_symlinks=False)
        opened = os.fstat(evidence.descriptor)
    except OSError as error:
        raise ManifestPathError("evidence_identity") from error
    identity = (evidence.device, evidence.inode)
    if (
        not stat.S_ISDIR(current.st_mode)
        or not stat.S_ISDIR(opened.st_mode)
        or (current.st_dev, current.st_ino) != identity
        or (opened.st_dev, opened.st_ino) != identity
    ):
        raise ManifestPathError("evidence_identity")
    if evidence.attempt_id is not None:
        if evidence.root_descriptor is None or evidence.head_sha is None:
            raise ManifestPathError("final_attempt_binding")
        if (
            evidence.root_path is None
            or evidence.root_device is None
            or evidence.root_inode is None
        ):
            raise ManifestPathError("final_attempt_binding")
        visible_root = evidence.root_path.stat(follow_symlinks=False)
        opened_root = os.fstat(evidence.root_descriptor)
        root_identity = (evidence.root_device, evidence.root_inode)
        repository_root = evidence.root_path.parents[2]
        if (
            (visible_root.st_dev, visible_root.st_ino) != root_identity
            or (opened_root.st_dev, opened_root.st_ino) != root_identity
            or _current_git_head(repository_root) != evidence.head_sha
        ):
            raise ManifestPathError("final_attempt_binding")
        try:
            verify_open_final_attempt_authority(
                evidence_root=evidence.root_path,
                evidence_root_fd=evidence.root_descriptor,
                expected_attempt_id=evidence.attempt_id,
                expected_attempt_path=evidence.path,
                expected_head=evidence.head_sha,
            )
        except FinalAttemptAuthorityError as error:
            raise ManifestPathError("final_attempt_binding") from error


def verify_evidence_directory(evidence: EvidenceDirectory) -> None:
    """Revalidate a bound destination immediately before producing resources."""
    _verify_evidence_directory(evidence)


def _verify_manifest_file(destination: Path, descriptor: int, evidence: EvidenceDirectory) -> None:
    """Ensure the manifest name still resolves to the safe descriptor that wrote it."""
    try:
        opened = os.fstat(descriptor)
        named = os.stat(destination.name, dir_fd=evidence.descriptor, follow_symlinks=False)
    except OSError as error:
        raise ManifestPathError("manifest_file") from error
    opened_identity = (opened.st_dev, opened.st_ino)
    named_identity = (named.st_dev, named.st_ino)
    safe_opened = (
        stat.S_ISREG(opened.st_mode)
        and opened.st_uid == os.geteuid()
        and opened.st_nlink == 1
        and not opened.st_mode & 0o022
    )
    safe_named = (
        stat.S_ISREG(named.st_mode)
        and named.st_uid == os.geteuid()
        and named.st_nlink == 1
        and not named.st_mode & 0o022
    )
    if not safe_opened or not safe_named or opened_identity != named_identity:
        raise ManifestPathError("manifest_file")


def _unlink_manifest_if_owned(
    destination: Path, descriptor: int, evidence: EvidenceDirectory
) -> None:
    """Remove only the visible leaf still bound to the writer's open descriptor."""
    try:
        opened = os.fstat(descriptor)
        named = os.stat(destination.name, dir_fd=evidence.descriptor, follow_symlinks=False)
    except OSError:
        return
    if (opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino):
        try:
            os.unlink(destination.name, dir_fd=evidence.descriptor)
            os.fsync(evidence.descriptor)
        except OSError:
            return


def write_manifest(
    destination: Path, payload: dict[str, object], evidence: EvidenceDirectory
) -> None:
    if destination.absolute().parent != evidence.path or destination.name in {"", ".", ".."}:
        raise ManifestPathError("manifest_boundary")
    _verify_evidence_directory(evidence)
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(destination.name, flags, 0o600, dir_fd=evidence.descriptor)
    try:
        _write_all(descriptor, encoded)
        os.fsync(descriptor)
        _verify_evidence_directory(evidence)
        _verify_manifest_file(destination, descriptor, evidence)
        os.fsync(evidence.descriptor)
        _verify_evidence_directory(evidence)
        _verify_manifest_file(destination, descriptor, evidence)
    except BaseException:
        with defer_cleanup_signals():
            _unlink_manifest_if_owned(destination, descriptor, evidence)
        raise
    finally:
        os.close(descriptor)


def read_manifest_bytes(path: Path) -> bytes:
    parent = open_evidence_directory(path.absolute().parent)
    try:
        try:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent.descriptor)
        except OSError as error:
            raise ManifestPathError("manifest_file") from error
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ManifestPathError("manifest_file")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 64 * 1024):
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    finally:
        close_evidence_directory(parent)


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("manifest_short_write")
        view = view[written:]


def cleanup_receipt_succeeded(outcome: dict[str, object]) -> bool:
    if not all(outcome.get(field) is True for field in _CLEANUP_FIELDS):
        return False
    observed = outcome.get("foreign_containers_observed")
    preserved = outcome.get("foreign_containers_preserved")
    return (observed is True and preserved is True) or (observed is False and preserved is None)


def _validate_final_postgres_manifest(
    destination: Path, evidence: EvidenceDirectory, mode: str
) -> None:
    if evidence.attempt_id is None:
        return
    nodes = {
        "all": "postgres-all",
        "migration-roundtrip": "postgres-migration-roundtrip",
        "stream-resume": "postgres-stream-resume",
        "run-lifecycle+stream-resume": "postgres-run-lifecycle-stream-resume",
        "queue-concurrency": "postgres-queue-concurrency",
    }
    node = nodes.get(mode)
    matched = re.fullmatch(
        r"f2-static\.([a-z][a-z0-9-]{0,63})\.([0-9a-f]{16})\.json",
        destination.name,
    )
    if node is None or matched is None or matched.group(1) != node:
        raise ManifestPathError("final_postgres_manifest")
    expected_slug = f"runtime-policy-final-{evidence.attempt_id}-f2-{matched.group(2)}"
    if os.environ.get("E2E_EXPORT_SLUG") != expected_slug:
        raise ManifestPathError("final_postgres_slug")


@contextmanager
def defer_cleanup_signals() -> Iterator[list[int]]:
    deferred: list[int] = []
    if threading.current_thread() is not threading.main_thread():
        yield deferred
        return
    previous = {number: signal.getsignal(number) for number in _SIGNALS}

    def record(number: int, _frame: object) -> None:
        deferred.append(number)

    for number in _SIGNALS:
        signal.signal(number, record)
    try:
        yield deferred
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def run_cli(run_scenario: ScenarioRunner, build_early_interrupt: EarlyInterruptBuilder) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "all",
            "migration-roundtrip",
            "self-test",
            "stream-resume",
            "run-lifecycle+stream-resume",
            "queue-concurrency",
        ),
    )
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    evidence_root = ensure_evidence_root(REPO_ROOT)
    destination = args.manifest if args.manifest.is_absolute() else REPO_ROOT / args.manifest
    if destination.absolute().parent == evidence_root.absolute():
        validate_manifest_destination(destination, evidence_root)
        evidence = open_evidence_directory(evidence_root)
    else:
        evidence = open_manifest_directory(destination, evidence_root, REPO_ROOT)
        try:
            _validate_final_postgres_manifest(destination, evidence, args.mode)
        except ManifestPathError:
            close_evidence_directory(evidence)
            raise
    process_id = os.getpid()
    process_identity = process_identity_sha256()
    previous_handlers = {number: signal.getsignal(number) for number in _SIGNALS}
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    manifest_mode = args.mode
    try:
        try:
            if args.mode in {
                "all",
                "migration-roundtrip",
                "stream-resume",
                "run-lifecycle+stream-resume",
                "queue-concurrency",
            }:
                verify_evidence_directory(evidence)
                scenarios = [
                    run_scenario(
                        args.mode, process_id=process_id, process_identity=process_identity
                    )
                ]
            else:
                scenarios = []
                for kind in ("success", "child_failure", "sigint"):
                    verify_evidence_directory(evidence)
                    scenario = run_scenario(
                        kind,
                        process_id=process_id,
                        process_identity=process_identity,
                    )
                    scenarios.append(scenario)
                    if scenario.get("status") == "interrupted":
                        break
            concurrent_pair = False
        except RunnerInterrupted as interrupted:
            early_mode: ExternalScenarioKind = (
                args.mode
                if args.mode
                in {
                    "migration-roundtrip",
                    "stream-resume",
                    "run-lifecycle+stream-resume",
                    "queue-concurrency",
                }
                else "all"
            )
            scenarios = [
                build_early_interrupt(
                    interrupted.signal_number,
                    process_id=process_id,
                    process_identity=process_identity,
                    mode=early_mode,
                )
            ]
            concurrent_pair = False
            manifest_mode = early_mode
        status = (
            "passed"
            if all(
                item["status"] == "passed" and cleanup_receipt_succeeded(item) for item in scenarios
            )
            else "failed"
        )
        if scenarios[-1].get("status") == "interrupted" and cleanup_receipt_succeeded(
            scenarios[-1]
        ):
            status = "interrupted"
        write_manifest(
            destination,
            {
                "schema_version": 1,
                "mode": manifest_mode,
                "status": status,
                "concurrent_pair": concurrent_pair,
                "scenarios": scenarios,
            },
            evidence,
        )
    finally:
        for number, handler in previous_handlers.items():
            signal.signal(number, handler)
        close_evidence_directory(evidence)
    if status == "failed":
        return 1
    interrupt_codes = [
        item.get("child_exit_code") for item in scenarios if item.get("status") == "interrupted"
    ]
    if interrupt_codes and isinstance(interrupt_codes[0], int):
        return interrupt_codes[0]
    return 0
