"""Artifact-export adapter and strict receipt parsing for isolated E2E."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from e2e_runner_contract import Lane, Project
from e2e_runner_runtime import REPO_ROOT, E2eResources
from postgres_runner_runtime import run_command

type ExportValue = str | int | bool | None | list[ExportValue] | dict[str, ExportValue]

MAX_EXPORT_FILE_BYTES = 20 * 1024 * 1024
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
EXPORT_FAILURE_PREFIX = "E2E_ARTIFACT_EXPORT_FAILURE:"
EXPORT_FAILURE_CATEGORIES = frozenset(
    {
        "source_topology",
        "unsupported_artifact",
        "secret_scan",
        "bounds",
        "manifest_publish",
        "internal",
    }
)


@dataclass(frozen=True, slots=True)
class ExportFile:
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ExportReceipt:
    secret_scan_passed: bool
    export_directory: str | None
    files: tuple[ExportFile, ...]
    screenshots: tuple[str, ...]
    schema_version: int = 1
    manifest: ExportFile | None = None
    failure_code: str | None = None


def _failure(code: str) -> ExportReceipt:
    return ExportReceipt(False, None, (), (), failure_code=code)


def _parse_process_failure(stdout: str, stderr: str) -> str:
    if stdout:
        return "exporter_process_failed"
    for category in EXPORT_FAILURE_CATEGORIES:
        if stderr == f"{EXPORT_FAILURE_PREFIX}{category}\n":
            return category
    return "exporter_process_failed"


def _safe_export_path(value: str) -> bool:
    if not value or "\\" in value or value.startswith("/"):
        return False
    path = PurePosixPath(value)
    return path.as_posix() == value and all(part not in {"", ".", ".."} for part in path.parts)


def _parse_export_file(value: ExportValue) -> ExportFile | None:
    if not isinstance(value, dict):
        return None
    file_path = value.get("path")
    sha256 = value.get("sha256")
    size_bytes = value.get("size_bytes")
    if (
        not isinstance(file_path, str)
        or not _safe_export_path(file_path)
        or not isinstance(sha256, str)
        or SHA256_PATTERN.fullmatch(sha256) is None
        or type(size_bytes) is not int
        or size_bytes < 0
        or size_bytes > MAX_EXPORT_FILE_BYTES
    ):
        return None
    return ExportFile(file_path, sha256, size_bytes)


def _decode_receipt(path: Path) -> ExportReceipt:
    try:
        decoded = json.loads(path.read_text())
        passed = decoded["secret_scan_passed"] is True
        schema_version = decoded["schema_version"]
        manifest_value = decoded["manifest"]
        directory = decoded["export_directory"]
        files = decoded["files"]
        screenshots = decoded["screenshots"]
    except (OSError, KeyError, json.JSONDecodeError, TypeError):
        return _failure("invalid_export_receipt")
    if (
        not isinstance(directory, str)
        or not isinstance(files, list)
        or not isinstance(screenshots, list)
    ):
        return _failure("invalid_export_receipt")
    parsed_files = tuple(_parse_export_file(value) for value in files)
    if any(file is None for file in parsed_files):
        return _failure("invalid_export_receipt")
    typed_files = tuple(file for file in parsed_files if file is not None)
    manifest = _parse_export_file(manifest_value)
    paths = [file.path for file in typed_files]
    valid = (
        schema_version == 1
        and manifest is not None
        and bool(typed_files)
        and typed_files[0] == manifest
        and len(paths) == len(set(paths))
        and paths[1:] == sorted(paths[1:])
        and all(isinstance(value, str) for value in screenshots)
    )
    if not valid:
        return _failure("invalid_export_receipt")
    return ExportReceipt(
        passed, directory, typed_files, tuple(screenshots), schema_version, manifest
    )


def export_artifacts(
    resources: E2eResources,
    lane: Lane,
    project: Project,
    secrets_to_scan: tuple[str, ...],
) -> ExportReceipt:
    del lane
    receipt_path = resources.run_root / "export-receipt.json"
    sources = (
        resources.run_root / "frontend/test-results" / project,
        resources.run_root / "output/captures",
        resources.run_root / "output/e2e-captures",
    )
    base_slug = os.environ.get("E2E_EXPORT_SLUG", project)[:48].rstrip("-")
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "TZ"}
    }
    env["E2E_EXPORT_SECRETS_JSON"] = json.dumps(list(secrets_to_scan))
    command = ["node", str(REPO_ROOT / "frontend/scripts/export-e2e-artifacts.mjs")]
    for source in sources:
        command.extend(("--source-dir", str(source)))
    command.extend(
        (
            "--run-root",
            str(resources.run_root),
            "--project",
            project,
            "--slug",
            f"{base_slug}-{resources.run_id[:12]}",
            "--repo-root",
            str(REPO_ROOT),
            "--receipt",
            str(receipt_path),
        )
    )
    result = run_command(command, env=env, timeout=120)
    if result.returncode != 0:
        return _failure(_parse_process_failure(result.stdout, result.stderr))
    return _decode_receipt(receipt_path)
