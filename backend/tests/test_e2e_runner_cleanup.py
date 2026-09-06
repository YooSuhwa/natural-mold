"""Artifact receipt adapter validation for the isolated E2E runner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import e2e_runner_export as export_module  # noqa: E402
from e2e_runner_cleanup import publish_runner_receipts  # noqa: E402
from e2e_runner_contract import Project, build_e2e_dsns  # noqa: E402
from e2e_runner_export import (  # noqa: E402
    EXPORT_FAILURE_CATEGORIES,
    EXPORT_FAILURE_PREFIX,
    MAX_EXPORT_FILE_BYTES,
    ExportValue,
    _decode_receipt,
    _parse_export_file,
    export_artifacts,
)
from e2e_runner_runtime import E2eResources  # noqa: E402
from postgres_runner_runtime import OwnedContainer  # noqa: E402


def _file(path: str = "results/execution.json") -> dict[str, ExportValue]:
    return {"path": path, "sha256": "a" * 64, "size_bytes": 10}


def _resources(run_root: Path) -> E2eResources:
    root_stat = run_root.stat()
    run_id = "a" * 24
    return E2eResources(
        run_id,
        run_root,
        root_stat.st_dev,
        root_stat.st_ino,
        OwnedContainer("owner", run_id, "container", "id", 54321),
        set(),
        build_e2e_dsns(password="password", port=54321, database=f"moldy_e2e_scripted_{run_id}"),
        "160001",
        "m70",
        "m70",
        "fingerprint",
        True,
    )


def test_export_file_accepts_safe_bounded_receipt() -> None:
    assert _parse_export_file(_file()) is not None


@pytest.mark.parametrize(
    "path",
    ["", "/absolute.json", "../escape.json", "results/../escape.json", "./result.json", "a\\b"],
)
def test_export_file_rejects_unsafe_paths(path: str) -> None:
    assert _parse_export_file(_file(path)) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sha256", "A" * 64),
        ("sha256", "z" * 64),
        ("size_bytes", True),
        ("size_bytes", -1),
        ("size_bytes", MAX_EXPORT_FILE_BYTES + 1),
    ],
)
def test_export_file_rejects_invalid_hash_or_size(field: str, value: ExportValue) -> None:
    receipt = _file()
    receipt[field] = value

    assert _parse_export_file(receipt) is None


def test_export_adapter_retains_empty_test_source_rejection(tmp_path: Path) -> None:
    manifest = _file("export-manifest.json")
    path = tmp_path / "export-receipt.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "secret_scan_passed": True,
                "export_directory": "output/e2e-captures/safe-rejection",
                "manifest": manifest,
                "files": [manifest],
                "screenshots": [],
                "source_rejection": {
                    "category": "secret_scan",
                    "rule_id": "sensitive_assignment",
                    "artifact_path": "results/execution.log",
                    "tests": [],
                },
            }
        )
    )

    receipt = _decode_receipt(path, "scripted-full")

    assert receipt.failure_code is None
    assert receipt.source_rejection is not None
    assert receipt.source_rejection.tests == ()


@pytest.mark.parametrize(
    "mutation",
    [
        {"unexpected": "value"},
        {"attempt_id": None},
        {"export_directory_absolute": "/tmp/mixed"},
    ],
)
def test_export_adapter_rejects_extra_and_mixed_schema_variants(
    tmp_path: Path, mutation: dict[str, ExportValue]
) -> None:
    manifest = _file("export-manifest.json")
    payload: dict[str, ExportValue] = {
        "schema_version": 1,
        "secret_scan_passed": True,
        "export_directory": "output/e2e-captures/legacy",
        "manifest": manifest,
        "files": [manifest],
        "screenshots": [],
    }
    payload.update(mutation)
    path = tmp_path / "export-receipt.json"
    path.write_text(json.dumps(payload))

    assert _decode_receipt(path, "scripted-full").failure_code == "invalid_export_receipt"


@pytest.mark.parametrize("kind", ["source_symlink", "destination_symlink", "source_hardlink"])
def test_receipt_publisher_rejects_link_attacks(tmp_path: Path, kind: str) -> None:
    runner = tmp_path / "runner"
    destination = tmp_path / "frontend/test-results/scripted-smoke"
    runner.mkdir()
    destination.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_text("outside")
    source = runner / "selection.json"
    if kind == "source_symlink":
        source.symlink_to(outside)
    elif kind == "source_hardlink":
        os.link(outside, source)
    else:
        source.write_text("safe")
        (destination / "selection.json").symlink_to(outside)

    resources = _resources(tmp_path)

    assert publish_runner_receipts(resources, "scripted-smoke") is False
    assert outside.read_text() == "outside"


@pytest.mark.parametrize("category", sorted(EXPORT_FAILURE_CATEGORIES))
def test_export_adapter_preserves_only_bounded_failure_category(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, category: str
) -> None:
    result = subprocess.CompletedProcess(["node"], 1, "", f"{EXPORT_FAILURE_PREFIX}{category}\n")
    monkeypatch.setattr(export_module, "run_command", lambda *_args, **_kwargs: result)
    resources = _resources(tmp_path)

    receipt = export_artifacts(
        resources,
        "scripted",
        "scripted-smoke",
        (),
    )

    assert receipt.failure_code == category


@pytest.mark.parametrize(
    ("stdout", "stderr"),
    [
        ("unexpected", f"{EXPORT_FAILURE_PREFIX}secret_scan\n"),
        ("", f"{EXPORT_FAILURE_PREFIX}unknown\n"),
        ("", f"{EXPORT_FAILURE_PREFIX}secret_scan\nleaked-detail"),
        ("", "Bearer malicious-secret\n"),
    ],
)
def test_export_adapter_rejects_malicious_or_noncanonical_failure_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stdout: str, stderr: str
) -> None:
    result = subprocess.CompletedProcess(["node"], 1, stdout, stderr)
    monkeypatch.setattr(export_module, "run_command", lambda *_args, **_kwargs: result)
    resources = _resources(tmp_path)

    receipt = export_artifacts(
        resources,
        "scripted",
        "scripted-smoke",
        (),
    )

    assert receipt.failure_code == "exporter_process_failed"


@pytest.mark.parametrize(
    ("project", "expected_sources"),
    [
        ("scripted-smoke", ("frontend/test-results/scripted-smoke",)),
        ("scripted-full", ("frontend/test-results/scripted-full",)),
        ("live-manual", ("frontend/test-results/live-manual",)),
        (
            "scripted-capture",
            (
                "frontend/test-results/scripted-capture",
                "output/captures",
                "output/e2e-captures",
            ),
        ),
    ],
)
def test_export_adapter_scopes_capture_sources_to_capture_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    project: Project,
    expected_sources: tuple[str, ...],
) -> None:
    captured_command: list[str] = []

    def record_command(
        command: list[str], **_kwargs: ExportValue
    ) -> subprocess.CompletedProcess[str]:
        captured_command.extend(command)
        return subprocess.CompletedProcess(command, 1, "", f"{EXPORT_FAILURE_PREFIX}internal\n")

    monkeypatch.setattr(export_module, "run_command", record_command)

    export_artifacts(_resources(tmp_path), "scripted", project, ())

    sources = tuple(
        Path(captured_command[index + 1]).relative_to(tmp_path).as_posix()
        for index, value in enumerate(captured_command)
        if value == "--source-dir"
    )
    assert sources == expected_sources


def test_final_attempt_export_slug_preserves_full_attempt_id_without_run_suffix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    attempt_id = "b" * 64
    slug = f"runtime-policy-final-{attempt_id}-capture"
    captured_command: list[str] = []

    def record_command(
        command: list[str], **_kwargs: ExportValue
    ) -> subprocess.CompletedProcess[str]:
        captured_command.extend(command)
        return subprocess.CompletedProcess(command, 1, "", f"{EXPORT_FAILURE_PREFIX}internal\n")

    monkeypatch.setenv("E2E_EXPORT_SLUG", slug)
    monkeypatch.setattr(export_module, "run_command", record_command)

    export_artifacts(_resources(tmp_path), "scripted", "scripted-capture", ())

    slug_index = captured_command.index("--slug")
    assert captured_command[slug_index + 1] == slug
