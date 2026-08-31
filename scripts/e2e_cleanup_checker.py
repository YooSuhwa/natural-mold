"""Stable public facade for independent isolated-E2E receipt validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from e2e_cleanup_contract import require
from e2e_cleanup_export import SECRET, validate_export
from e2e_cleanup_lifecycle import validate_lifecycle, validate_live_absence
from e2e_cleanup_outcome import validate_cleanup, validate_egress, validate_outcome
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
    validate_outcome(payload, lane, project)
    validate_export(payload.get("export"), project, repository_root)
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
