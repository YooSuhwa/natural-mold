"""Stable public facade for independent isolated-E2E receipt validation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Final, Literal

from e2e_cleanup_contract import require
from e2e_cleanup_export import SECRET, validate_export
from e2e_cleanup_lifecycle import validate_lifecycle, validate_live_absence
from e2e_cleanup_outcome import validate_cleanup, validate_egress, validate_outcome
from e2e_failure_diagnostics import FailureDiagnosticError, parse_failure_diagnostics
from e2e_runner_contract import FINAL_CAPTURE_SPECS
from e2e_runner_manifest import FINAL_E2E_EXPORT_KEYS, FINAL_E2E_TOP_KEYS
from postgres_cleanup_checker import ManifestValidationError, load_manifest
from project_gate_catalog import CATALOG

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
type ArtifactScope = Literal["manifest-only", "full"]
_EMPTY_PREEXECUTION_EXPORT: Final = {
    "schema_version": 1,
    "secret_scan_passed": False,
    "failure_code": None,
    "export_directory": None,
    "manifest": None,
    "files": [],
    "screenshots": [],
    "source_rejection": None,
}
_PREEXECUTION_TOP_KEYS: Final = frozenset(
    {
        "schema_version",
        "runner",
        "lane",
        "project",
        "workers",
        "retries",
        "reuse_existing_server",
        "status",
        "failure_reason",
        "child_exit_code",
        "self_test",
        "run_id",
        "owned_run_root",
        "owned_database",
        "owned_backend",
        "owned_frontend",
        "owned_proxy",
        "postgres_image",
        "server_version_num",
        "alembic_head",
        "alembic_current",
        "schema_fingerprint",
        "second_upgrade_idempotent",
        "frontend_port",
        "backend_port",
        "selected_ids",
        "executed_ids",
        "unexpected_failures",
        "export",
        "egress",
        "cleanup",
    }
)


def _empty_preexecution_export(export: object) -> bool:
    """Recognize the only exporter-free receipt accepted before scripted-smoke selection."""
    return isinstance(export, dict) and export == _EMPTY_PREEXECUTION_EXPORT


def _final_selection(
    directory: object,
    attempt_id: str,
    lane: str,
    project: str,
    receipt_path: Path | None,
) -> tuple[str, ...] | None:
    if not isinstance(directory, str):
        return None
    f3 = {
        "scripted-full": ("scripted", "scripted", ()),
        "scripted-capture": ("scripted", "capture", FINAL_CAPTURE_SPECS),
        "live-manual": ("live", "live", ()),
    }.get(project)
    if f3 is not None:
        expected_lane, suffix, specs = f3
        expected_directory = re.compile(
            rf"^output/e2e-captures/[0-9]{{8}}-runtime-policy-final-{attempt_id}-{suffix}$"
        )
        if lane == expected_lane and expected_directory.fullmatch(directory):
            return specs
    matched = re.fullmatch(
        rf"output/e2e-captures/[0-9]{{8}}-runtime-policy-final-{re.escape(attempt_id)}-f2-([0-9a-f]{{16}})",
        directory,
    )
    if matched is None or lane != "scripted" or receipt_path is None:
        return None
    receipt = re.fullmatch(
        r"f2-static\.([a-z][a-z0-9-]{0,63})\.([0-9a-f]{16})\.json",
        receipt_path.name,
    )
    if receipt is None or receipt.group(2) != matched.group(1):
        return None
    if receipt_path.parent.name != attempt_id:
        return None
    node = CATALOG.get(receipt.group(1))
    if node is None or node.kind != "e2e" or node.argv[0] != project:
        return None
    return node.argv[1:]


def _valid_skipped_ids(value: object, project: str) -> bool:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return False
    values = [item for item in value if isinstance(item, str)]
    return len(values) == len(set(values)) and all(
        0 < len(item) <= 2048
        and item.startswith(f"{project}::e2e/")
        and item.count("::") == 2
        and not any(ord(character) < 32 or ord(character) == 127 for character in item)
        for item in values
    )


def validate_payload(
    payload: dict[str, object],
    *,
    repository_root: Path = REPO_ROOT,
    receipt_path: Path | None = None,
) -> ArtifactScope:
    """Validate logical E2E facts and their persistent redacted export."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    require(SECRET.search(encoded) is None, "secret_material")
    require(
        payload.get("schema_version") == 1 and payload.get("runner") == "moldy-isolated-e2e",
        "runner",
    )
    lane, project, database_owned = validate_lifecycle(payload)
    export_value = payload.get("export")
    diagnostic_export = _empty_preexecution_export(export_value)
    rejection = (
        None if diagnostic_export else validate_export(export_value, project, repository_root)
    )
    export = export_value if isinstance(export_value, dict) else {}
    top_current_keys = {
        "attempt_id",
        "export_directory_absolute",
        "export_tree_sha256",
        "screenshots",
    }
    nested_current_keys = {
        "attempt_id",
        "export_directory_absolute",
        "export_tree_sha256",
        "screenshots_absolute",
    }
    present_top = set(payload) & top_current_keys
    present_nested = set(export) & nested_current_keys
    require(
        (present_top == set() and present_nested == set())
        or (present_top == top_current_keys and present_nested == nested_current_keys),
        "export_schema_variant",
    )
    directory = export.get("export_directory")
    final_variant = isinstance(directory, str) and "runtime-policy-final-" in directory
    final_variant = final_variant or bool(
        set(payload) & {"head_sha", "requested_specs", "skipped_ids"}
    )
    if final_variant:
        require(present_nested == nested_current_keys, "final_attempt_export")
        require(set(payload) == FINAL_E2E_TOP_KEYS, "final_receipt_schema")
        require(set(export) == FINAL_E2E_EXPORT_KEYS, "final_export_schema")
    if present_nested:
        require(payload.get("attempt_id") == export.get("attempt_id"), "attempt_id")
        require(
            payload.get("export_directory_absolute") == export.get("export_directory_absolute"),
            "export_directory_absolute",
        )
        require(
            payload.get("export_tree_sha256") == export.get("export_tree_sha256"),
            "export_tree",
        )
        require(payload.get("screenshots") == export.get("screenshots_absolute"), "screenshots")
        if final_variant:
            attempt_id = export.get("attempt_id")
            expected_specs = (
                _final_selection(directory, attempt_id, lane, project, receipt_path)
                if isinstance(attempt_id, str)
                else None
            )
            skipped = payload.get("skipped_ids")
            selected = payload.get("selected_ids")
            executed = payload.get("executed_ids")
            require(
                isinstance(attempt_id, str)
                and re.fullmatch(r"[0-9a-f]{64}", attempt_id) is not None
                and re.fullmatch(r"[0-9a-f]{40}", str(payload.get("head_sha"))) is not None
                and expected_specs is not None
                and payload.get("requested_specs") == list(expected_specs)
                and _valid_skipped_ids(skipped, project)
                and isinstance(skipped, list)
                and isinstance(selected, list)
                and isinstance(executed, list)
                and not set(skipped) & set(selected)
                and not set(skipped) & set(executed)
                and (payload.get("status") != "passed" or skipped == []),
                "final_attempt_export",
            )
    diagnostics_value = payload.get("unexpected_failures")
    if "unexpected_failures" not in payload and (
        payload.get("status") == "passed" or payload.get("self_test") != "normal"
    ):
        diagnostics = ()
    else:
        try:
            diagnostics = parse_failure_diagnostics(diagnostics_value, project)
        except FailureDiagnosticError as error:
            raise ManifestValidationError("failure_diagnostics") from error
    if rejection is not None:
        require(
            tuple((item.node_id, item.status) for item in rejection.tests)
            == tuple((item.node_id, item.status) for item in diagnostics),
            "source_rejection",
        )
    diagnostic_preexecution = validate_outcome(
        payload, lane, project, source_rejected=rejection is not None
    )
    require(diagnostic_preexecution is diagnostic_export, "preexecution_export")
    if diagnostic_preexecution:
        require(set(payload) == _PREEXECUTION_TOP_KEYS, "preexecution_schema")
    if diagnostic_preexecution or payload.get("status") == "passed":
        require(not diagnostics and rejection is None, "failure_diagnostics")
    elif payload.get("self_test") == "normal":
        if payload.get("failure_reason") == "artifact_export_failed":
            require(not diagnostics and rejection is not None, "failure_diagnostics")
        else:
            require(bool(diagnostics), "failure_diagnostics")
    else:
        require(rejection is None, "source_rejection")
    executed = payload.get("executed_ids")
    require(
        isinstance(executed, list) and all(item.node_id in executed for item in diagnostics),
        "failure_diagnostics",
    )
    validate_egress(payload.get("egress"), lane)
    validate_cleanup(
        payload,
        database_owned,
        diagnostic_preexecution=diagnostic_preexecution,
    )
    return "manifest-only" if diagnostic_preexecution else "full"


def load_and_validate(path: Path, *, repository_root: Path = REPO_ROOT) -> None:
    """Read through the shared no-follow boundary before independent E2E validation."""
    payload = load_manifest(path)
    validate_payload(payload, repository_root=repository_root, receipt_path=path)
    validate_live_absence(payload)


__all__ = [
    "ManifestValidationError",
    "load_and_validate",
    "validate_live_absence",
    "validate_payload",
]
