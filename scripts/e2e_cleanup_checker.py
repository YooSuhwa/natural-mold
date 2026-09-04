"""Stable public facade for independent isolated-E2E receipt validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from e2e_cleanup_contract import require
from e2e_cleanup_export import SECRET, validate_export
from e2e_cleanup_lifecycle import validate_lifecycle, validate_live_absence
from e2e_cleanup_outcome import validate_cleanup, validate_egress, validate_outcome
from e2e_failure_diagnostics import FailureDiagnosticError, parse_failure_diagnostics
from postgres_cleanup_checker import ManifestValidationError, load_manifest

REPO_ROOT: Final = Path(__file__).resolve().parents[1]


def validate_payload(payload: dict[str, object], *, repository_root: Path = REPO_ROOT) -> None:
    """Validate logical E2E facts and their persistent redacted export."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    require(SECRET.search(encoded) is None, "secret_material")
    require(
        payload.get("schema_version") == 1 and payload.get("runner") == "moldy-isolated-e2e",
        "runner",
    )
    lane, project, database_owned = validate_lifecycle(payload)
    rejection = validate_export(payload.get("export"), project, repository_root)
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
    if payload.get("status") == "passed":
        require(not diagnostics and rejection is None, "failure_diagnostics")
    elif payload.get("self_test") == "normal":
        if payload.get("failure_reason") == "artifact_export_failed":
            require(not diagnostics and rejection is not None, "failure_diagnostics")
        else:
            require(bool(diagnostics), "failure_diagnostics")
    else:
        require(rejection is None, "source_rejection")
    validate_outcome(payload, lane, project, source_rejected=rejection is not None)
    executed = payload.get("executed_ids")
    require(
        isinstance(executed, list) and all(item.node_id in executed for item in diagnostics),
        "failure_diagnostics",
    )
    validate_egress(payload.get("egress"), lane)
    validate_cleanup(payload, database_owned)


def load_and_validate(path: Path) -> None:
    """Read through the shared no-follow boundary before independent E2E validation."""
    payload = load_manifest(path)
    validate_payload(payload)
    validate_live_absence(payload)


__all__ = [
    "ManifestValidationError",
    "load_and_validate",
    "validate_live_absence",
    "validate_payload",
]
