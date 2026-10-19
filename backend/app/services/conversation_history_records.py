from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.message_event import MessageEvent
from app.schemas.conversation_history import JsonValue
from app.services import trace_storage
from app.services.conversation_history_projection import (
    canonical_payload_sha256,
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
    rows: list[EventProjection] = []
    for record in records:
        events = sanitize_event_payloads(await trace_storage.load_events(db, record), secrets)
        rows.append(
            EventProjection(
                record.assistant_msg_id,
                events,
                canonical_payload_sha256(events),
            )
        )
    return rows


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
