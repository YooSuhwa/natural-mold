from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.agent_runtime.protocol_egress import project_and_redact_protocol_data


def redact_debug_trace_value(
    value: Any,
    *,
    secret_values: Sequence[str] | None = None,
) -> Any:
    """Project internal runtime references before redacting trace egress data."""
    return project_and_redact_protocol_data(
        "debug_traces",
        value,
        secret_values=secret_values,
    )


def redacted_debug_mapping(
    value: dict[str, Any],
    *,
    secret_values: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return a safe mapping for trace API serialization."""
    redacted = redact_debug_trace_value(value, secret_values=secret_values)
    return redacted if isinstance(redacted, dict) else {}


def redacted_debug_rows(
    rows: list[dict[str, Any]],
    *,
    secret_values: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Return safe Langfuse observation rows without mutating the source rows."""
    redacted = redact_debug_trace_value(rows, secret_values=secret_values)
    if not isinstance(redacted, list):
        return []
    return [row for row in redacted if isinstance(row, dict)]
