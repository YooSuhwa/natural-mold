from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.models.message_event import MessageEvent
from app.observability.langfuse import fetch_langfuse_observations, is_langfuse_enabled
from app.schemas.conversation import DebugTraceSpan, DebugTraceSummary


def _duration_ms(started: datetime | None, ended: datetime | None) -> int | None:
    if not started or not ended:
        return None
    return max(0, int((ended - started).total_seconds() * 1000))


def _parse_dt(value: Any) -> datetime | None:
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


def _event_usage_total(record: MessageEvent) -> int | None:
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


def _source_from_events(record: MessageEvent) -> str:
    for event in record.events or []:
        data = event.get("data") if isinstance(event, dict) else None
        if isinstance(data, dict) and isinstance(data.get("source"), str):
            return data["source"]
    return "chat"


def summary_from_record(
    record: MessageEvent,
    *,
    fallback_reason: str | None = None,
) -> DebugTraceSummary:
    trace_id = record.external_trace_id or record.assistant_msg_id
    provider = record.external_trace_provider or "message_events"
    return DebugTraceSummary(
        trace_id=trace_id,
        provider=provider,
        name=f"agent.{_source_from_events(record)}",
        status=record.status,
        source=_source_from_events(record),
        started_at=record.created_at,
        completed_at=record.completed_at,
        duration_ms=_duration_ms(record.created_at, record.completed_at),
        total_tokens=_event_usage_total(record),
        moldy_run_id=record.assistant_msg_id,
        langfuse_url=record.external_trace_url,
        fallback=provider != "langfuse" or bool(fallback_reason),
        fallback_reason=fallback_reason,
    )


def _event_kind(event_name: str) -> str:
    if event_name == "error":
        return "error"
    if event_name.startswith("tool_call") or event_name.startswith("tool_result"):
        return "tool"
    if event_name.startswith("message"):
        return "llm"
    if "skill" in event_name:
        return "skill"
    return "event"


def spans_from_message_events(record: MessageEvent) -> list[DebugTraceSpan]:
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
            duration_ms=_duration_ms(record.created_at, record.completed_at),
            input={"provider": "message_events"},
            output=_last_message_output(record),
            metadata={
                "moldy_run_id": record.assistant_msg_id,
                "external_trace_provider": record.external_trace_provider,
                "external_trace_id": record.external_trace_id,
            },
        )
    ]

    for index, event in enumerate(record.events or [], start=1):
        event_name = str(event.get("event") or "event")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        status = "failed" if event_name == "error" else record.status
        spans.append(
            DebugTraceSpan(
                id=str(event.get("id") or f"{record.assistant_msg_id}:event:{index}"),
                parent_id=root_id,
                name=str(data.get("name") or event_name),
                kind=_event_kind(event_name),
                status=status,
                started_at=record.created_at,
                ended_at=record.completed_at,
                duration_ms=None,
                input=data.get("args") or data.get("input"),
                output=data.get("result") or data.get("output") or data.get("content"),
                metadata={"event": event_name, "sequence": index, "data": data},
            )
        )
    return spans


def _last_message_output(record: MessageEvent) -> dict[str, Any] | None:
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


def spans_from_observations(rows: list[dict[str, Any]]) -> list[DebugTraceSpan]:
    spans: list[DebugTraceSpan] = []
    for index, row in enumerate(rows, start=1):
        started = _parse_dt(row.get("startTime") or row.get("start_time"))
        ended = _parse_dt(row.get("endTime") or row.get("end_time"))
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
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
                duration_ms=_duration_ms(started, ended),
                input=row.get("input"),
                output=row.get("output"),
                metadata={
                    **metadata,
                    "model": row.get("providedModelName") or row.get("model"),
                    "usage": row.get("usageDetails") or row.get("usage"),
                },
            )
        )
    return spans


async def build_debug_detail(
    record: MessageEvent,
) -> tuple[DebugTraceSummary, list[DebugTraceSpan], list[dict[str, Any]] | None, str | None]:
    should_fetch_langfuse = bool(
        is_langfuse_enabled()
        and record.external_trace_provider == "langfuse"
        and record.external_trace_id
    )
    error = None
    if should_fetch_langfuse and record.external_trace_id:
        rows, error = await fetch_langfuse_observations(record.external_trace_id)
        if rows:
            return summary_from_record(record), spans_from_observations(rows), rows, None

    fallback_reason = error or (
        None
        if is_langfuse_enabled() and record.external_trace_id
        else "Langfuse trace unavailable"
    )
    return (
        summary_from_record(record, fallback_reason=fallback_reason),
        spans_from_message_events(record),
        None,
        fallback_reason,
    )
