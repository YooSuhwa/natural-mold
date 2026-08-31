"""Shared parsed-value helpers and immutable E2E lifecycle policy."""

from __future__ import annotations

import re
from typing import Final

from postgres_cleanup_checker import ManifestValidationError

HEX_64: Final = re.compile(r"[0-9a-f]{64}")
RUN_ID: Final = re.compile(r"[0-9a-f]{24}")
PORTS: Final = {"scripted": (3100, 8101), "live": (3200, 8201)}
PROJECTS: Final = {
    "scripted": {"scripted-smoke", "scripted-full", "scripted-capture"},
    "live": {"live-manual"},
}
CLEANUP_FIELDS: Final = (
    "cleanup_container_removed",
    "owned_label_absent",
    "postgres_port_removed",
    "owned_database_removed",
    "backend_port_removed",
    "frontend_port_removed",
    "proxy_port_removed",
    "process_group_stopped",
    "cleanup_run_root_removed",
)
LIVE_NODES: Final = (
    "live-manual::e2e/agent-triggers.spec.ts::a created interval trigger renders in the "
    "settings triggers tab",
    "live-manual::e2e/builder.spec.ts::starts a session and runs the build pipeline from an "
    "initial message",
    "live-manual::e2e/operator-screens.spec.ts::System LLM shows the seed-configured role slots",
    "live-manual::e2e/operator-screens.spec.ts::creates and deletes a system credential "
    "through the catalog modal",
)


def require(condition: bool, reason: str) -> None:
    """Raise only a stable reason without reflecting untrusted receipt content."""
    if not condition:
        raise ManifestValidationError(reason)


def mapping(value: object, reason: str) -> dict[str, object]:
    """Parse a JSON object with string keys at the checker boundary."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ManifestValidationError(reason)
    return {key: item for key, item in value.items() if isinstance(key, str)}


def string(value: object, reason: str) -> str:
    """Parse a non-coerced JSON string."""
    if not isinstance(value, str):
        raise ManifestValidationError(reason)
    return value


def integer(value: object, reason: str) -> int:
    """Parse an integer while rejecting booleans."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ManifestValidationError(reason)
    return value


def strings(value: object, reason: str) -> list[str]:
    """Parse a list of non-coerced JSON strings."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ManifestValidationError(reason)
    return list(value)
