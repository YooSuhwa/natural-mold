"""Persistent E2E export receipt and export-manifest validation."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Final

from e2e_cleanup_contract import HEX_64, integer, mapping, require, string, strings
from e2e_cleanup_export_paths import (
    MAX_FILE_BYTES,
    MAX_TOTAL_BYTES,
    read_regular,
    safe_export_directory,
    safe_relative,
    walk_export,
)
from e2e_failure_diagnostics import (
    FailureDiagnosticError,
    SourceRejection,
    parse_source_rejection,
)
from postgres_cleanup_checker import ManifestValidationError

SECRET: Final = re.compile(
    rb"(?:postgres(?:ql)?(?:\+[^:]*)?://|(?:api[_-]?key|password|authorization|cookie|token)"
    rb"\s*[:=]|bearer\s+[a-z0-9._~-]+|\bsk-[a-z0-9_-]{12,}|\bAKIA[0-9A-Z]{16}\b)",
    re.IGNORECASE,
)


def _mappings(value: object, reason: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ManifestValidationError(reason)
    return [mapping(item, reason) for item in value]


def _artifact_file(value: object) -> tuple[str, str, int]:
    entry = mapping(value, "export_file")
    require(set(entry) == {"path", "sha256", "size_bytes"}, "export_file")
    path = safe_relative(entry.get("path"), "export_file_path").as_posix()
    digest = string(entry.get("sha256"), "export_file_hash")
    size = integer(entry.get("size_bytes"), "export_file_size")
    require(
        HEX_64.fullmatch(digest) is not None and 0 <= size <= MAX_FILE_BYTES,
        "export_file_size",
    )
    return path, digest, size


def _validate_export_manifest(
    content: bytes, project: str, listed: list[tuple[str, str, int]]
) -> SourceRejection | None:
    try:
        decoded = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestValidationError("export_manifest_json") from error
    manifest = mapping(decoded, "export_manifest")
    require(
        manifest.get("schema_version") == 1 and manifest.get("project") == project,
        "export_manifest",
    )
    policy = mapping(manifest.get("policy"), "export_manifest_policy")
    require(
        policy
        == {
            "version": 1,
            "screenshots": "scripted-capture-only",
            "max_file_bytes": MAX_FILE_BYTES,
            "max_total_bytes": MAX_TOTAL_BYTES,
        },
        "export_manifest_policy",
    )
    scan = mapping(manifest.get("secret_scan"), "export_secret_scan")
    require(
        scan.get("passed") is True
        and integer(scan.get("exact_secret_count"), "export_secret_scan") >= 0,
        "export_secret_scan",
    )
    files = [
        _artifact_file(value) for value in _mappings(manifest.get("files"), "export_manifest_files")
    ]
    expected = sorted(item for item in listed if item[0] != "export-manifest.json")
    require(files == expected, "export_manifest_files")
    total = mapping(manifest.get("total"), "export_manifest_total")
    require(
        total
        == {
            "file_count": len(expected),
            "size_bytes": sum(item[2] for item in expected),
        },
        "export_manifest_total",
    )
    rejection_value = manifest.get("source_rejection")
    if rejection_value is None:
        return None
    try:
        rejection = parse_source_rejection(rejection_value, project)
    except FailureDiagnosticError as error:
        raise ManifestValidationError("source_rejection") from error
    require(not expected, "source_rejection_files")
    return rejection


def validate_export(export: object, project: str, repository_root: Path) -> None:
    """Verify an ignored, persistent export against its receipt and internal manifest."""
    receipt = mapping(export, "export")
    require(
        receipt.get("schema_version") == 1 and receipt.get("secret_scan_passed") is True,
        "export",
    )
    relative = safe_relative(receipt.get("export_directory"), "export_directory")
    require(relative.parts[:2] == ("output", "e2e-captures"), "export_directory")
    ignore_file = repository_root / ".gitignore"
    require(ignore_file.is_file(), "export_ignore")
    try:
        ignored = ignore_file.read_text()
    except OSError as error:
        raise ManifestValidationError("export_ignore") from error
    require(re.search(r"(?m)^/?output/?$", ignored) is not None, "export_ignore")
    directory = safe_export_directory(repository_root, relative)
    listed = [_artifact_file(item) for item in _mappings(receipt.get("files"), "export_files")]
    paths = [item[0] for item in listed]
    require(len(paths) == len(set(paths)), "export_files")
    require(sum(item[2] for item in listed) <= MAX_TOTAL_BYTES, "export_total_size")
    manifest_entry = _artifact_file(receipt.get("manifest"))
    require(
        bool(listed)
        and listed[0] == manifest_entry
        and manifest_entry[0] == "export-manifest.json"
        and [item[0] for item in listed[1:]] == sorted(item[0] for item in listed[1:]),
        "export_manifest",
    )
    require(walk_export(directory) == set(paths), "export_extra_files")
    content_by_path: dict[str, bytes] = {}
    for path, digest, size in listed:
        content = read_regular(directory.joinpath(*PurePosixPath(path).parts))
        require(
            len(content) == size and hashlib.sha256(content).hexdigest() == digest,
            "export_file_hash",
        )
        require(SECRET.search(content) is None, "secret_material")
        content_by_path[path] = content
    rejection = _validate_export_manifest(content_by_path["export-manifest.json"], project, listed)
    receipt_rejection_value = receipt.get("source_rejection")
    require(
        (receipt_rejection_value is None) == (rejection is None),
        "source_rejection",
    )
    if rejection is not None:
        try:
            receipt_rejection = parse_source_rejection(receipt_rejection_value, project)
        except FailureDiagnosticError as error:
            raise ManifestValidationError("source_rejection") from error
        require(receipt_rejection == rejection, "source_rejection")
    screenshots = strings(receipt.get("screenshots"), "export_screenshots")
    expected_screenshots = [path for path in paths if path.lower().endswith(".png")]
    require(
        screenshots == expected_screenshots and len(screenshots) == len(set(screenshots)),
        "export_screenshots",
    )
    require(not screenshots or project == "scripted-capture", "export_screenshots")
    require(
        rejection is None or (listed == [manifest_entry] and not screenshots),
        "source_rejection",
    )
