"""Identity-safe aggregate I/O and child receipt validation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Final, TypedDict

from e2e_failure_diagnostics import (
    FailureDiagnosticError,
    parse_failure_diagnostics,
    parse_source_rejection,
)
from project_gate_runtime import JSONObject, JSONValue, ProjectGateError

MAX_RECEIPT_BYTES: Final = 4 * 1024 * 1024
STATIC_KEYS: Final = frozenset(
    {"schema_version", "status", "child_exit_code", "cleanup", "run_root_sha256"}
)
POSTGRES_CLEANUP_KEYS: Final = (
    "cleanup_container_removed",
    "owned_label_absent",
    "port_mapping_removed",
    "process_group_stopped",
    "cleanup_run_root_removed",
)
E2E_CLEANUP_KEYS: Final = (
    "cleanup_container_removed",
    "owned_label_absent",
    "postgres_port_removed",
    "owned_database_removed",
    "backend_port_removed",
    "frontend_port_removed",
    "proxy_port_removed",
    "process_group_stopped",
    "cleanup_run_root_removed",
    "foreign_containers_preserved",
)


class ReceiptSummary(TypedDict):
    relative_path: str
    sha256: str
    cleanup_passed: bool
    secret_scan_passed: bool | None
    workers: int | None
    retries: int | None
    screenshot_count: int | None


def new_child_path(aggregate: Path, node_id: str, token: str) -> Path:
    """Build a safe, unique sibling receipt path without creating it."""
    return aggregate.with_name(f"{aggregate.stem}.{node_id}.{token}.json")


def _read_receipt(path: Path) -> tuple[JSONObject, str]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ProjectGateError("invalid_child_receipt") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size <= 0
            or metadata.st_size > MAX_RECEIPT_BYTES
        ):
            raise ProjectGateError("invalid_child_receipt")
        data = os.read(descriptor, metadata.st_size + 1)
    finally:
        os.close(descriptor)
    try:
        parsed = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectGateError("invalid_child_receipt") from error
    if not isinstance(parsed, dict):
        raise ProjectGateError("invalid_child_receipt")
    return parsed, hashlib.sha256(data).hexdigest()


def _summary(
    path: Path,
    repo_root: Path,
    digest: str,
    *,
    cleanup: bool,
    secret_scan: bool | None = None,
    workers: int | None = None,
    retries: int | None = None,
    screenshot_count: int | None = None,
) -> ReceiptSummary:
    return {
        "relative_path": path.relative_to(repo_root).as_posix(),
        "sha256": digest,
        "cleanup_passed": cleanup,
        "secret_scan_passed": secret_scan,
        "workers": workers,
        "retries": retries,
        "screenshot_count": screenshot_count,
    }


def validate_static(path: Path, repo_root: Path, expected_exit: int) -> ReceiptSummary:
    payload, digest = _read_receipt(path)
    valid = (
        set(payload) == STATIC_KEYS
        and payload.get("schema_version") == 1
        and payload.get("status") == ("passed" if expected_exit == 0 else "failed")
        and payload.get("child_exit_code") == expected_exit
        and payload.get("cleanup") == "removed"
        and isinstance(payload.get("run_root_sha256"), str)
        and len(str(payload["run_root_sha256"])) == 64
    )
    if not valid:
        raise ProjectGateError("invalid_child_receipt")
    return _summary(path, repo_root, digest, cleanup=True)


def validate_postgres(path: Path, repo_root: Path, mode: str, expected_exit: int) -> ReceiptSummary:
    payload, digest = _read_receipt(path)
    scenarios = payload.get("scenarios")
    scenario = scenarios[0] if isinstance(scenarios, list) and len(scenarios) == 1 else None
    receipt = scenario.get("test_receipt") if isinstance(scenario, dict) else None
    zero_selection_debt = (
        isinstance(receipt, dict)
        and isinstance(receipt.get("selected_node_ids"), list)
        and bool(receipt["selected_node_ids"])
        and receipt.get("selected_node_ids") == receipt.get("executed_node_ids")
        and receipt.get("failed_node_ids") == []
        and receipt.get("skipped_node_ids") == []
        and receipt.get("deselected_node_ids") == []
    )
    migration_roundtrip = (
        isinstance(scenario, dict)
        and mode == "migration-roundtrip"
        and "test_receipt" in scenario
        and scenario.get("test_receipt") is None
        and scenario.get("migration_roundtrip") is True
        and scenario.get("child_exit_code") == 0
    )
    valid = (
        payload.get("schema_version") == 1
        and payload.get("mode") == mode
        and payload.get("status") == ("passed" if expected_exit == 0 else "failed")
        and isinstance(scenario, dict)
        and scenario.get("scenario") == mode
        and scenario.get("child_exit_code") == expected_exit
        and all(scenario.get(key) is True for key in POSTGRES_CLEANUP_KEYS)
        and (migration_roundtrip or (mode != "migration-roundtrip" and zero_selection_debt))
    )
    if not valid:
        raise ProjectGateError("invalid_child_receipt")
    return _summary(path, repo_root, digest, cleanup=True)


def _safe_screenshots(value: JSONValue) -> list[str] | None:
    if not isinstance(value, list):
        return None
    screenshots: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        screenshots.append(item)
    safe = re.compile(r"^captures/[A-Za-z0-9][A-Za-z0-9._/-]*\.png$")
    if all(safe.fullmatch(item) and ".." not in item.split("/") for item in screenshots):
        return screenshots
    return None


def _safe_node_ids(value: JSONValue) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return [item for item in value if isinstance(item, str)]


def validate_e2e(
    path: Path,
    repo_root: Path,
    expected_exit: int,
    *,
    project: str,
    expected_spec: str | tuple[str, ...] | None,
    expected_screenshot_count: int | None = None,
) -> ReceiptSummary:
    payload, digest = _read_receipt(path)
    selected = _safe_node_ids(payload.get("selected_ids"))
    executed = _safe_node_ids(payload.get("executed_ids"))
    export = payload.get("export")
    cleanup = payload.get("cleanup")
    screenshots = _safe_screenshots(export.get("screenshots")) if isinstance(export, dict) else None
    diagnostics_value = payload.get("unexpected_failures", [])
    try:
        diagnostics = parse_failure_diagnostics(diagnostics_value, project)
        rejection = (
            parse_source_rejection(export.get("source_rejection"), project)
            if isinstance(export, dict) and export.get("source_rejection") is not None
            else None
        )
    except FailureDiagnosticError as error:
        raise ProjectGateError("invalid_child_receipt") from error
    expected_specs = (
        ()
        if expected_spec is None
        else (expected_spec,)
        if isinstance(expected_spec, str)
        else expected_spec
    )
    expected_prefixes = tuple(f"{project}::{spec}::" for spec in expected_specs)
    selection_values = selected is not None and executed is not None
    selected_nodes = selected or []
    executed_nodes = executed or []
    executed_strings = set(executed_nodes)
    selection_matches = not expected_prefixes or (
        all(item.startswith(expected_prefixes) for item in selected_nodes)
        and all(item.startswith(expected_prefixes) for item in executed_nodes)
        and all(
            any(item.startswith(expected_prefix) for item in selected_nodes)
            for expected_prefix in expected_prefixes
        )
        and all(
            any(item.startswith(expected_prefix) for item in executed_nodes)
            for expected_prefix in expected_prefixes
        )
    )
    screenshots_match = screenshots == [] if project == "scripted-full" else screenshots is not None
    if project == "scripted-capture":
        screenshots_match = (
            expected_screenshot_count is not None
            and screenshots is not None
            and len(screenshots) == expected_screenshot_count
        )
    rejection_matches_diagnostics = rejection is None or tuple(
        (item.node_id, item.status) for item in rejection.tests
    ) == tuple((item.node_id, item.status) for item in diagnostics)
    artifact_export_failure = (
        expected_exit != 0
        and rejection is not None
        and not rejection.tests
        and not diagnostics
        and payload.get("failure_reason") == "artifact_export_failed"
        and payload.get("child_exit_code") == 0
    )
    child_exit_matches = payload.get("child_exit_code") == expected_exit or artifact_export_failure
    failure_evidence_matches = (
        not diagnostics and rejection is None
        if expected_exit == 0
        else bool(diagnostics) or artifact_export_failure
    )
    rejection_nodes_match = all(
        item.node_id in executed_strings for item in (() if rejection is None else rejection.tests)
    )
    valid = (
        payload.get("runner") == "moldy-isolated-e2e"
        and payload.get("lane") == "scripted"
        and payload.get("project") == project
        and payload.get("workers") == 1
        and payload.get("retries") == 0
        and payload.get("status") == ("passed" if expected_exit == 0 else "failed")
        and child_exit_matches
        and selection_values
        and bool(selected_nodes)
        and selected_nodes == executed_nodes
        and len(selected_nodes) == len(set(selected_nodes))
        and selection_matches
        and isinstance(export, dict)
        and export.get("secret_scan_passed") is True
        and screenshots_match
        and ("unexpected_failures" in payload or expected_exit == 0)
        and failure_evidence_matches
        and rejection_matches_diagnostics
        and all(item.node_id in executed_strings for item in diagnostics)
        and rejection_nodes_match
        and isinstance(cleanup, dict)
        and all(cleanup.get(key) is True for key in E2E_CLEANUP_KEYS)
    )
    if not valid:
        raise ProjectGateError("invalid_child_receipt")
    return _summary(
        path,
        repo_root,
        digest,
        cleanup=True,
        secret_scan=True,
        workers=1,
        retries=0,
        screenshot_count=len(screenshots) if screenshots is not None else None,
    )
