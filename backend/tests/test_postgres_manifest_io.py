"""File-descriptor trust-boundary contracts for PostgreSQL manifests."""

from __future__ import annotations

import json
import signal
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT.parent / "scripts"))

from postgres_cleanup_checker import ManifestValidationError, load_manifest  # noqa: E402
from postgres_manifest_io import (  # noqa: E402
    ManifestPathError,
    _write_all,
    cleanup_receipt_succeeded,
    close_evidence_directory,
    open_evidence_directory,
    write_manifest,
)
from postgres_runner_contract import (  # noqa: E402
    DsnContractError,
    ensure_evidence_root,
    validate_manifest_destination,
)
from postgres_runner_runtime import ScenarioKind, cleanup_run_root  # noqa: E402


def _cleanup_receipt(
    *, foreign_observed: bool, foreign_preserved: bool | None
) -> dict[str, object]:
    return {
        "cleanup_container_removed": True,
        "owned_label_absent": True,
        "port_mapping_removed": True,
        "process_group_stopped": True,
        "cleanup_run_root_removed": True,
        "foreign_containers_observed": foreign_observed,
        "foreign_containers_preserved": foreign_preserved,
    }


def test_interrupted_receipt_rejects_missing_after_snapshot() -> None:
    # Given an interrupt after its before-snapshot was captured but after-snapshot failed.
    scenario = _cleanup_receipt(foreign_observed=True, foreign_preserved=None)

    # When cleanup completeness is evaluated, then early-unobserved allowance does not apply.
    assert cleanup_receipt_succeeded(scenario) is False


def test_main_writes_checker_eligible_parent_interrupt_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given a parent-runner scenario interrupted before any test receipt exists.
    interrupted = postgres_test_runner._early_interrupt_outcome(
        signal.SIGINT,
        process_id=999999,
        process_identity="d" * 64,
    )
    captured: list[dict[str, object]] = []
    dummy = postgres_manifest_io.EvidenceDirectory(tmp_path, -1, 1, 1)
    monkeypatch.setattr(postgres_manifest_io, "ensure_evidence_root", lambda _repo: tmp_path)
    monkeypatch.setattr(
        postgres_manifest_io, "validate_manifest_destination", lambda path, _root: path
    )
    monkeypatch.setattr(postgres_manifest_io, "open_evidence_directory", lambda _root: dummy)
    monkeypatch.setattr(postgres_manifest_io, "close_evidence_directory", lambda _root: None)
    monkeypatch.setattr(postgres_manifest_io, "process_identity_sha256", lambda: "d" * 64)
    monkeypatch.setattr(postgres_test_runner, "_run_scenario", lambda _kind, **_kw: interrupted)
    monkeypatch.setattr(
        postgres_manifest_io,
        "write_manifest",
        lambda _path, payload, _root: captured.append(payload),
    )
    monkeypatch.setattr(
        sys, "argv", ["postgres_test_runner.py", "all", "--manifest", str(tmp_path / "x.json")]
    )

    # When the main entrypoint serializes that parent cancellation.
    exit_code = postgres_test_runner.main()

    # Then it retains signal status instead of recasting it as an ordinary failure.
    assert exit_code == 130
    assert captured[0]["status"] == "interrupted"
    assert captured[0]["scenarios"] == [interrupted]


def test_run_cli_recasts_passed_scenario_when_after_snapshot_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given a passed child whose post-cleanup Docker snapshot is unavailable.
    scenario: dict[str, object] = {
        "scenario": "all",
        "status": "passed",
        "child_exit_code": 0,
    }
    scenario.update(_cleanup_receipt(foreign_observed=True, foreign_preserved=None))
    captured: list[dict[str, object]] = []
    dummy = postgres_manifest_io.EvidenceDirectory(tmp_path, -1, 1, 1)
    monkeypatch.setattr(postgres_manifest_io, "ensure_evidence_root", lambda _repo: tmp_path)
    monkeypatch.setattr(
        postgres_manifest_io, "validate_manifest_destination", lambda path, _root: path
    )
    monkeypatch.setattr(postgres_manifest_io, "open_evidence_directory", lambda _root: dummy)
    monkeypatch.setattr(postgres_manifest_io, "close_evidence_directory", lambda _root: None)
    monkeypatch.setattr(postgres_manifest_io, "process_identity_sha256", lambda: "d" * 64)
    monkeypatch.setattr(
        postgres_manifest_io,
        "write_manifest",
        lambda _path, payload, _root: captured.append(payload),
    )
    monkeypatch.setattr(
        sys, "argv", ["postgres_test_runner.py", "all", "--manifest", str(tmp_path / "x.json")]
    )

    def interrupted_scenario(
        kind: ScenarioKind, *, process_id: int, process_identity: str
    ) -> dict[str, object]:
        del kind, process_id, process_identity
        return scenario

    # When the CLI computes the top-level status.
    exit_code = postgres_manifest_io.run_cli(
        interrupted_scenario,
        postgres_test_runner._early_interrupt_outcome,
    )

    # Then incomplete post-cleanup verification forces a failed top-level receipt.
    assert exit_code == 1
    assert captured[0]["status"] == "failed"


def test_manifest_destination_accepts_new_file_beneath_evidence(tmp_path: Path) -> None:
    # Given a repository-local evidence root and a new descendant.
    evidence = tmp_path / ".omo" / "evidence" / "project-restart-consolidated-roadmap"
    evidence.mkdir(parents=True)
    destination = evidence / "result.json"

    # When the destination is validated.
    result = validate_manifest_destination(destination, evidence)

    # Then the exact new path is returned without creating the final file.
    assert result == destination
    assert not destination.exists()


def test_manifest_destination_rejects_existing_or_symlink_path(tmp_path: Path) -> None:
    # Given an occupied path and a symlink beneath the evidence root.
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    occupied = evidence / "occupied.json"
    occupied.write_text("old")
    target = evidence / "target.json"
    target.write_text("target")
    linked = evidence / "linked.json"
    linked.symlink_to(target)

    # When each destination is validated, then replacement is refused.
    with pytest.raises(FileExistsError):
        validate_manifest_destination(occupied, evidence)
    with pytest.raises(FileExistsError):
        validate_manifest_destination(linked, evidence)


def test_cleanup_run_root_refuses_symlink_replacement(tmp_path: Path) -> None:
    # Given a private root whose original directory was replaced by a symlink.
    run_root = tmp_path / "run"
    run_root.mkdir()
    identity = run_root.stat()
    run_root.rmdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    sentinel = foreign / "sentinel"
    sentinel.write_text("preserve")
    run_root.symlink_to(foreign, target_is_directory=True)

    # When cleanup verifies the original device/inode identity.
    removed = cleanup_run_root(run_root, identity.st_dev, identity.st_ino)

    # Then it refuses the replacement and preserves foreign content.
    assert removed is False
    assert run_root.is_symlink()
    assert sentinel.read_text() == "preserve"


def test_ensure_evidence_root_rejects_symlink_component(tmp_path: Path) -> None:
    # Given a repository whose .omo component points outside the repository.
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".omo").symlink_to(foreign, target_is_directory=True)

    # When the evidence root is prepared, then traversal is rejected.
    with pytest.raises(DsnContractError, match="evidence_component"):
        ensure_evidence_root(repository)
    assert list(foreign.iterdir()) == []


def test_write_all_retries_short_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given an OS writer that accepts at most two bytes per call.
    captured = bytearray()

    def short_write(_descriptor: int, payload: bytes | memoryview) -> int:
        chunk = bytes(payload[:2])
        captured.extend(chunk)
        return len(chunk)

    monkeypatch.setattr("postgres_manifest_io.os.write", short_write)

    # When an atomic-manifest payload is written.
    _write_all(99, b"abcdef")

    # Then every byte is emitted in order despite short writes.
    assert bytes(captured) == b"abcdef"


def test_initial_outcome_records_process_identity_before_docker_observation(tmp_path: Path) -> None:
    # Given a new parent-runner lifecycle before Docker has been called.
    outcome = postgres_test_runner._initial_outcome(
        "all", "a" * 24, tmp_path, process_id=999999, process_identity="d" * 64
    )

    # When the initial receipt is constructed.

    # Then process identity is available while storage and port facts remain unobserved.
    assert outcome["process_id"] == 999999
    assert outcome["process_identity_sha256"] == "d" * 64
    assert outcome["tmpfs_storage"] is False
    assert outcome["storage_inspected"] is False
    assert outcome["port_mapping_observed"] is False
    assert "container_id" not in outcome
    assert "port" not in outcome
    assert outcome["run_root_created"] is True
    assert outcome["warning_scan_complete"] is False


def test_write_manifest_rejects_replaced_evidence_root(tmp_path: Path) -> None:
    # Given a trusted evidence directory whose path is replaced after it is opened.
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    trusted = open_evidence_directory(evidence)
    displaced = tmp_path / "displaced"
    evidence.rename(displaced)
    evidence.mkdir()

    try:
        # When a manifest write revalidates the held descriptor against the path.
        with pytest.raises(ManifestPathError, match="evidence_identity"):
            write_manifest(evidence / "result.json", {"status": "passed"}, trusted)
    finally:
        close_evidence_directory(trusted)

    # Then neither the attacker-controlled replacement nor the displaced root receives the file.
    assert not (evidence / "result.json").exists()
    assert not (displaced / "result.json").exists()


@pytest.mark.parametrize("failure", ["write", "file_fsync", "directory_fsync"])
def test_write_manifest_removes_partial_leaf_after_interruption(
    failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an owned destination interrupted during write or durability synchronization.
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    destination = evidence / "result.json"
    trusted = open_evidence_directory(evidence)
    real_write_all = postgres_manifest_io._write_all
    real_fsync = postgres_manifest_io.os.fsync
    fsync_calls = 0

    def interrupted_write(descriptor: int, payload: bytes) -> None:
        if failure == "write":
            postgres_manifest_io.os.write(descriptor, payload[:2])
            raise postgres_manifest_io.RunnerInterrupted(signal.SIGINT)
        real_write_all(descriptor, payload)

    def interrupted_fsync(descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        target = 1 if failure == "file_fsync" else 2
        if failure != "write" and fsync_calls == target:
            raise postgres_manifest_io.RunnerInterrupted(signal.SIGINT)
        real_fsync(descriptor)

    monkeypatch.setattr(postgres_manifest_io, "_write_all", interrupted_write)
    monkeypatch.setattr(postgres_manifest_io.os, "fsync", interrupted_fsync)
    try:
        # When manifest emission is interrupted after O_EXCL creation.
        with pytest.raises(postgres_manifest_io.RunnerInterrupted):
            write_manifest(destination, {"status": "passed"}, trusted)
    finally:
        close_evidence_directory(trusted)

    # Then no incomplete destination survives through the pinned directory.
    assert not destination.exists()


def test_load_manifest_reads_once_from_nofollow_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a regular manifest and path helpers that must not participate in loading it.
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1}))

    def forbidden_path_probe(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("path_reopened")

    monkeypatch.setattr(Path, "is_file", forbidden_path_probe)
    monkeypatch.setattr(Path, "is_symlink", forbidden_path_probe)
    monkeypatch.setattr(Path, "read_text", forbidden_path_probe)

    # When the checker crosses the filesystem boundary.
    loaded = load_manifest(manifest)

    # Then JSON comes from the single opened regular-file descriptor.
    assert loaded == {"schema_version": 1}


def test_load_manifest_rejects_symlink_leaf(tmp_path: Path) -> None:
    # Given a manifest leaf that redirects to another regular file.
    target = tmp_path / "target.json"
    target.write_text("{}")
    linked = tmp_path / "linked.json"
    linked.symlink_to(target)

    # When the checker opens the leaf with no-follow semantics, then it rejects the redirect.
    with pytest.raises(ManifestValidationError, match="manifest_file"):
        load_manifest(linked)


import postgres_manifest_io  # noqa: E402
import postgres_test_runner  # noqa: E402
