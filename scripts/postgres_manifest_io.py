"""Trusted manifest I/O and lifecycle receipt helpers."""

from __future__ import annotations

import argparse
import json
import os
import signal
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

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
        or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)
    ):
        os.close(descriptor)
        raise ManifestPathError("evidence_identity")
    return EvidenceDirectory(path, descriptor, actual.st_dev, actual.st_ino)


def close_evidence_directory(evidence: EvidenceDirectory) -> None:
    os.close(evidence.descriptor)


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
        try:
            _write_all(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(evidence.descriptor)
    except BaseException:
        with defer_cleanup_signals():
            with suppress(OSError):
                os.unlink(destination.name, dir_fd=evidence.descriptor)
            with suppress(OSError):
                os.fsync(evidence.descriptor)
        raise


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
    parser.add_argument("mode", choices=("all", "self-test", "stream-resume"))
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    ensure_evidence_root(REPO_ROOT)
    destination = args.manifest if args.manifest.is_absolute() else REPO_ROOT / args.manifest
    validate_manifest_destination(destination, EVIDENCE_ROOT)
    evidence = open_evidence_directory(EVIDENCE_ROOT)
    process_id = os.getpid()
    process_identity = process_identity_sha256()
    previous_handlers = {number: signal.getsignal(number) for number in _SIGNALS}
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    manifest_mode = args.mode
    try:
        try:
            if args.mode in {"all", "stream-resume"}:
                scenarios = [
                    run_scenario(
                        args.mode, process_id=process_id, process_identity=process_identity
                    )
                ]
            else:
                scenarios = []
                for kind in ("success", "child_failure", "sigint"):
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
                "stream-resume" if args.mode == "stream-resume" else "all"
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
