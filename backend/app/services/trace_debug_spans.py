from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.models.message_event import MessageEvent
from app.schemas.conversation import DebugTraceSpan, DebugTraceSummary
from app.services.trace_debug_projection import redact_debug_trace_value, redacted_debug_mapping


def duration_ms(started: datetime | None, ended: datetime | None) -> int | None:
    """Return a non-negative duration when both timestamps are available."""
    if not started or not ended:
        return None
    return max(0, int((ended - started).total_seconds() * 1000))


def parse_dt(value: Any) -> datetime | None:
    """Normalize a Langfuse timestamp to the database's naive UTC convention."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not isinstance(value, str) or not value:
        return None
    try:
        return (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            .astimezone(UTC)
            .replace(tzinfo=None)
        )
    except ValueError:
        return None


def event_usage_total(record: MessageEvent) -> int | None:
    """Extract the last token total recorded for a message event."""
    for event in reversed(record.events or []):
        data = event.get("data") if isinstance(event, dict) else None
        if not isinstance(data, dict):
            continue
        usage = data.get("usage")
        if not isinstance(usage, dict):
            continue
        total = usage.get("total_tokens")
        if isinstance(total, int):
            return total
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if isinstance(prompt, int) or isinstance(completion, int):
            return int(prompt or 0) + int(completion or 0)
    return None


def source_from_events(
    record: MessageEvent,
    *,
    secret_values: Sequence[str] | None = None,
) -> str:
    """Return the first safe source label recorded for a message event."""
    for event in record.events or []:
        data = event.get("data") if isinstance(event, dict) else None
        if isinstance(data, dict) and isinstance(data.get("source"), str):
            public_source = redact_debug_trace_value(data["source"], secret_values=secret_values)
            return public_source if isinstance(public_source, str) else "chat"
    return "chat"


def summary_from_record(
    record: MessageEvent,
    *,
    fallback_reason: str | None = None,
    run_status: str | None = None,
    secret_values: Sequence[str] | None = None,
) -> DebugTraceSummary:
    """Build the public trace summary from persisted message-event metadata."""
    trace_id = record.external_trace_id or record.assistant_msg_id
    provider = record.external_trace_provider or "message_events"
    source = source_from_events(record, secret_values=secret_values)
    return DebugTraceSummary(
        trace_id=trace_id,
        provider=provider,
        name=f"agent.{source}",
        status=run_status or record.status,
        source=source,
        started_at=record.created_at,
        completed_at=record.completed_at,
        duration_ms=duration_ms(record.created_at, record.completed_at),
        total_tokens=event_usage_total(record),
        moldy_run_id=record.assistant_msg_id,
        langfuse_url=record.external_trace_url,
        fallback=provider != "langfuse" or bool(fallback_reason),
        fallback_reason=fallback_reason,
    )


def event_kind(event_name: str) -> str:
    """Map a persisted event name to the trace UI's span kind."""
    if event_name == "error":
        return "error"
    if event_name.startswith("tool_call") or event_name.startswith("tool_result"):
        return "tool"
    if event_name.startswith("message"):
        return "llm"
    if "skill" in event_name:
        return "skill"
    return "event"


def root_input_from_events(record: MessageEvent) -> Any | None:
    """Return the persisted root input for a fallback message-event trace."""
    for event in record.events or []:
        if event.get("event") != "message_start":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            return None
        return data.get("input")
    return None


def last_message_output(record: MessageEvent) -> dict[str, Any] | None:
    """Return the final message payload for a fallback message-event trace."""
    for event in reversed(record.events or []):
        if event.get("event") != "message_end":
            continue
        data = event.get("data")
        if isinstance(data, dict):
            return {
                "content": data.get("content"),
                "usage": data.get("usage"),
                "status": data.get("status") or record.status,
            }
    return None


def spans_from_message_events(
    record: MessageEvent,
    *,
    secret_values: Sequence[str] | None = None,
) -> list[DebugTraceSpan]:
    """Build safe fallback spans from persisted message events."""
    root_id = f"{record.assistant_msg_id}:root"
    spans = [
        DebugTraceSpan(
            id=root_id,
            parent_id=None,
            name="Moldy assistant turn",
            kind="workflow",
            status=record.status,
            started_at=record.created_at,
            ended_at=record.completed_at,
            duration_ms=duration_ms(record.created_at, record.completed_at),
            input=redact_debug_trace_value(
                root_input_from_events(record), secret_values=secret_values
            ),
            output=redact_debug_trace_value(
                last_message_output(record), secret_values=secret_values
            ),
            metadata=redacted_debug_mapping(
                {
                    "moldy_run_id": record.assistant_msg_id,
                    "provider": "message_events",
                    "external_trace_provider": record.external_trace_provider,
                    "external_trace_id": record.external_trace_id,
                },
                secret_values=secret_values,
            ),
        )
    ]
    for index, event in enumerate(record.events or [], start=1):
        event_name = str(event.get("event") or "event")
        raw_data = event.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        fallback_span_id = f"{record.assistant_msg_id}:event:{index}"
        public_span_id = redact_debug_trace_value(
            str(event.get("id") or fallback_span_id), secret_values=secret_values
        )
        public_span_name = redact_debug_trace_value(
            str(data.get("name") or event_name), secret_values=secret_values
        )
        spans.append(
            DebugTraceSpan(
                id=public_span_id if isinstance(public_span_id, str) else fallback_span_id,
                parent_id=root_id,
                name=public_span_name if isinstance(public_span_name, str) else "event",
                kind=event_kind(event_name),
                status="failed" if event_name == "error" else record.status,
                started_at=record.created_at,
                ended_at=record.completed_at,
                duration_ms=None,
                input=redact_debug_trace_value(
                    data.get("args") or data.get("input"), secret_values=secret_values
                ),
                output=redact_debug_trace_value(
                    data.get("result") or data.get("output") or data.get("content"),
                    secret_values=secret_values,
                ),
                metadata=redacted_debug_mapping(
                    {"event": event_name, "sequence": index, "data": data},
                    secret_values=secret_values,
                ),
            )
        )
    return spans


def spans_from_observations(
    rows: list[dict[str, Any]],
    *,
    secret_values: Sequence[str] | None = None,
) -> list[DebugTraceSpan]:
    """Build safe trace spans from already-projected Langfuse observations."""
    spans: list[DebugTraceSpan] = []
    for index, row in enumerate(rows, start=1):
        started = parse_dt(row.get("startTime") or row.get("start_time"))
        ended = parse_dt(row.get("endTime") or row.get("end_time"))
        raw_metadata = row.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        spans.append(
            DebugTraceSpan(
                id=str(row.get("id") or f"observation:{index}"),
                parent_id=(
                    str(row.get("parentObservationId"))
                    if row.get("parentObservationId")
                    else row.get("parent_observation_id")
                ),
                name=str(row.get("name") or row.get("type") or "observation"),
                kind=str(row.get("type") or "span").lower(),
                status=str(row.get("level") or row.get("status") or "completed").lower(),
                started_at=started,
                ended_at=ended,
                duration_ms=duration_ms(started, ended),
                input=redact_debug_trace_value(row.get("input"), secret_values=secret_values),
                output=redact_debug_trace_value(row.get("output"), secret_values=secret_values),
                metadata=redacted_debug_mapping(
                    {
                        **metadata,
                        "model": row.get("providedModelName") or row.get("model"),
                        "usage": row.get("usageDetails") or row.get("usage"),
                    },
                    secret_values=secret_values,
                ),
            )
        )
    span_ids = {span.id for span in spans}
    for span in spans:
        if span.parent_id and span.parent_id not in span_ids:
            span.parent_id = None
    return spans
