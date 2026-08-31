from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.models.message_event import MessageEvent
from app.observability.langfuse import fetch_langfuse_observations, is_langfuse_enabled
from app.schemas.conversation import DebugTraceSpan, DebugTraceSummary
from app.services.trace_debug_projection import redacted_debug_rows
from app.services.trace_debug_spans import (
    spans_from_message_events,
    spans_from_observations,
    summary_from_record,
)


async def build_debug_detail(
    record: MessageEvent,
    *,
    run_status: str | None = None,
    secret_values: Sequence[str] | None = None,
) -> tuple[DebugTraceSummary, list[DebugTraceSpan], list[dict[str, Any]] | None, str | None]:
    """Fetch a safe Langfuse trace or derive safe fallback spans from message events."""
    should_fetch_langfuse = bool(
        is_langfuse_enabled()
        and record.external_trace_provider == "langfuse"
        and record.external_trace_id
    )
    error = None
    if should_fetch_langfuse and record.external_trace_id:
        rows, error = await fetch_langfuse_observations(record.external_trace_id)
        if rows:
            safe_rows = redacted_debug_rows(rows, secret_values=secret_values)
            return (
                summary_from_record(record, run_status=run_status, secret_values=secret_values),
                spans_from_observations(safe_rows, secret_values=secret_values),
                safe_rows,
                None,
            )
    fallback_reason = error or (
        None if is_langfuse_enabled() and record.external_trace_id else "Langfuse trace unavailable"
    )
    return (
        summary_from_record(
            record,
            fallback_reason=fallback_reason,
            run_status=run_status,
            secret_values=secret_values,
        ),
        spans_from_message_events(record, secret_values=secret_values),
        None,
        fallback_reason,
    )
