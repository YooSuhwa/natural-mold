from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.message_event import MessageEvent, MessageEventChunk
from app.schemas.conversation_history import JsonValue
from app.services import trace_storage
from app.services.conversation_history_projection import (
    retained_event_sha256,
    sanitize_event_payloads,
)
from app.services.conversation_history_types import EventProjection


async def build_event_projections(
    db: AsyncSession, conversation_id: UUID, secrets: set[str]
) -> list[EventProjection]:
    records = list(
        await db.scalars(
            select(MessageEvent)
            .where(MessageEvent.conversation_id == conversation_id)
            .order_by(MessageEvent.created_at, MessageEvent.assistant_msg_id)
        )
    )
    # Group positions once. The legacy history loader already handles inline
    # prefixes and deduplicates retried IDs; keep its data and sanitation policy.
    positions: dict[tuple[UUID, str], list[tuple[int, int, int]]] = defaultdict(list)
    chunk = MessageEventChunk
    parts = await db.execute(
        select(
            chunk.message_event_id,
            chunk.assistant_msg_id,
            chunk.seq_start,
            chunk.seq_end,
            func.json_array_length(chunk.events),
        )
        .where(chunk.conversation_id == conversation_id)
        .order_by(chunk.seq_start, chunk.created_at, chunk.id)
    )
    for parent, run, first, last, count in parts:
        positions[parent, run].append((first, last, count))
    rows: list[EventProjection] = []
    for record in records:
        events = sanitize_event_payloads(await trace_storage.load_events(db, record), secrets)
        spans = _retained_spans(
            len(record.events or []), positions[record.id, record.assistant_msg_id], len(events)
        )
        offset = 0
        for start, length in spans:
            retained = events[offset : offset + length]
            rows.append(
                EventProjection(
                    record.assistant_msg_id, retained, retained_event_sha256(retained, start), start
                )
            )
            offset += length
    return rows


def _retained_spans(
    prefix_size: int, chunks: Iterable[tuple[int, int, int]], event_count: int
) -> list[tuple[int, int]]:
    """Preserve zero-based source offsets when a failed flush left a hole."""
    spans = [(0, prefix_size)] if prefix_size else []
    previous_end = prefix_size
    for first, last, count in chunks:
        if first <= previous_end or count < 1 or count != last - first + 1:
            # The old history payload remains readable, but no cursor may
            # replay an overlap or inconsistent source as a contiguous turn.
            return [(1, event_count)]
        if spans and first == previous_end + 1:
            start, length = spans[-1]
            spans[-1] = (start, length + count)
        else:
            spans.append((first - 1, count))
        previous_end = last
    if sum(length for _, length in spans) != event_count:
        # A historical retry with duplicate IDs was removed by load_events.
        return [(1, event_count)]
    return spans or [(0, 0)]


async def build_retained_trace(
    db: AsyncSession, conversation_id: UUID, secrets: set[str]
) -> dict[str, JsonValue]:
    event_rows = await db.execute(
        select(
            MessageEvent.assistant_msg_id,
            MessageEvent.external_trace_provider,
            MessageEvent.external_trace_id,
            MessageEvent.external_trace_url,
        )
        .where(MessageEvent.conversation_id == conversation_id)
        .order_by(MessageEvent.assistant_msg_id)
    )
    binding_rows = await db.execute(
        select(
            McpAppInvocationBinding.id,
            McpAppInvocationBinding.run_id,
            McpAppInvocationBinding.resource_uri,
            McpAppInvocationBinding.tool_meta,
            McpAppInvocationBinding.result_meta,
        )
        .where(McpAppInvocationBinding.conversation_id == conversation_id)
        .order_by(McpAppInvocationBinding.id)
    )
    raw: list[dict[str, JsonValue]] = [
        {
            "assistant_message_id": assistant_id,
            "provider": provider,
            "trace_id": trace_id,
            "trace_url": trace_url,
        }
        for assistant_id, provider, trace_id, trace_url in event_rows
    ]
    raw.extend(
        {
            "binding_id": str(binding_id),
            "run_id": str(run_id),
            "resource_uri": resource_uri,
            "tool_meta": tool_meta,
            "result_meta": result_meta,
        }
        for binding_id, run_id, resource_uri, tool_meta, result_meta in binding_rows
    )
    return {"references": sanitize_event_payloads(raw, secrets)}


__all__ = ["build_event_projections", "build_retained_trace"]
