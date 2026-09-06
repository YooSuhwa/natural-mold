"""Artifact-export adapter and strict receipt parsing for isolated E2E."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from e2e_cleanup_export_paths import read_regular
from e2e_failure_diagnostics import (
    FailureDiagnosticError,
    SourceRejection,
    parse_source_rejection,
)
from e2e_runner_contract import Lane, Project
from e2e_runner_playwright import PlaywrightOutcome
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
    source_rejection: SourceRejection | None = None
    attempt_id: str | None = None
    export_directory_absolute: str | None = None
    export_tree_sha256: str | None = None
    screenshots_absolute: tuple[str, ...] = ()


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


def _decode_receipt(path: Path, project: Project) -> ExportReceipt:
    try:
        decoded = json.loads(read_regular(path))
        passed = decoded["secret_scan_passed"] is True
        schema_version = decoded["schema_version"]
        manifest_value = decoded["manifest"]
        directory = decoded["export_directory"]
        files = decoded["files"]
        screenshots = decoded["screenshots"]
        rejection_value = decoded.get("source_rejection")
        attempt_id = decoded.get("attempt_id")
        directory_absolute = decoded.get("export_directory_absolute")
        tree_sha256 = decoded.get("export_tree_sha256")
        screenshots_absolute = decoded.get("screenshots_absolute")
    except (OSError, KeyError, json.JSONDecodeError, TypeError):
        return _failure("invalid_export_receipt")
    base_keys = {
        "schema_version",
        "secret_scan_passed",
        "export_directory",
        "manifest",
        "files",
        "screenshots",
    }
    current_keys = {
        "attempt_id",
        "export_directory_absolute",
        "export_tree_sha256",
        "screenshots_absolute",
    }
    optional_keys = {"source_rejection"}
    present_current = set(decoded) & current_keys
    if (
        not isinstance(decoded, dict)
        or not base_keys.issubset(decoded)
        or set(decoded) - base_keys - current_keys - optional_keys
        or present_current not in (set(), current_keys)
    ):
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
    try:
        rejection = (
            parse_source_rejection(rejection_value, project)
            if rejection_value is not None
            else None
        )
    except FailureDiagnosticError:
        return _failure("invalid_export_receipt")
    paths = [file.path for file in typed_files]
    fallback_shape = rejection is None or (
        len(typed_files) == 1 and typed_files[0] == manifest and screenshots == []
    )
    legacy_metadata = not present_current
    current_metadata = (
        (
            attempt_id is None
            or (isinstance(attempt_id, str) and SHA256_PATTERN.fullmatch(attempt_id))
        )
        and isinstance(directory_absolute, str)
        and Path(directory_absolute).is_absolute()
        and Path(directory_absolute) == REPO_ROOT / directory
        and isinstance(tree_sha256, str)
        and SHA256_PATTERN.fullmatch(tree_sha256) is not None
        and isinstance(screenshots_absolute, list)
        and all(
            isinstance(value, str) and Path(value).is_absolute() for value in screenshots_absolute
        )
        and screenshots_absolute == [str(Path(directory_absolute) / value) for value in screenshots]
    )
    valid = (
        schema_version == 1
        and manifest is not None
        and bool(typed_files)
        and typed_files[0] == manifest
        and len(paths) == len(set(paths))
        and paths[1:] == sorted(paths[1:])
        and all(isinstance(value, str) for value in screenshots)
        and fallback_shape
        and (legacy_metadata or current_metadata)
    )
    if not valid:
        return _failure("invalid_export_receipt")
    expected_tree = hashlib.sha256(
        json.dumps(
            [
                {"path": item.path, "sha256": item.sha256, "size_bytes": item.size_bytes}
                for item in typed_files
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if current_metadata and tree_sha256 != expected_tree:
        return _failure("invalid_export_receipt")
    return ExportReceipt(
        passed,
        directory,
        typed_files,
        tuple(screenshots),
        schema_version,
        manifest,
        source_rejection=rejection,
        attempt_id=attempt_id,
        export_directory_absolute=directory_absolute,
        export_tree_sha256=tree_sha256,
        screenshots_absolute=tuple(screenshots_absolute or ()),
    )


def export_artifacts(
    resources: E2eResources,
    lane: Lane,
    project: Project,
    secrets_to_scan: tuple[str, ...],
    failure_diagnostics: tuple[PlaywrightOutcome, ...] = (),
) -> ExportReceipt:
    del lane
    receipt_path = resources.run_root / "export-receipt.json"
    sources = [resources.run_root / "frontend/test-results" / project]
    if project == "scripted-capture":
        sources.extend(
            (
                resources.run_root / "output/captures",
                resources.run_root / "output/e2e-captures",
            )
        )
    base_slug = os.environ.get("E2E_EXPORT_SLUG", project)
    final_match = re.fullmatch(
        r"runtime-policy-final-([0-9a-f]{64})-"
        r"(?:scripted|capture|live|f2-[0-9a-f]{16})",
        base_slug,
    )
    slug = base_slug if final_match else f"{base_slug}-{resources.run_id[:12]}"
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "TZ"}
    }
    env["E2E_EXPORT_SECRETS_JSON"] = json.dumps(list(secrets_to_scan))
    if failure_diagnostics:
        env["E2E_EXPORT_FAILURE_DIAGNOSTICS_JSON"] = json.dumps(
            [
                {"node_id": diagnostic.node_id, "status": diagnostic.status}
                for diagnostic in failure_diagnostics
            ]
        )
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
            slug,
            "--repo-root",
            str(REPO_ROOT),
            "--receipt",
            str(receipt_path),
        )
    )
    result = run_command(command, env=env, timeout=120)
    if result.returncode != 0:
        return _failure(_parse_process_failure(result.stdout, result.stderr))
    return _decode_receipt(receipt_path, project)
