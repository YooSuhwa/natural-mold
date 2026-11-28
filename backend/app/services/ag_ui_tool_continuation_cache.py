"""Short-lived proof of a completed predecessor journal, local to one HTTP reader."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any
from uuid import UUID

import orjson
from sqlalchemy import String, Text, cast, func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.agent_runtime.ag_ui_v1_issuance import ToolContinuation
from app.models.message_event import MessageEvent, MessageEventChunk


@dataclass(slots=True)
class ContinuationCache:
    entries: dict[UUID, tuple[tuple[object, ...], tuple[ToolContinuation, ...]]] = field(
        default_factory=dict
    )


async def committed_source_digest(db: AsyncSession, room: UUID, run: UUID) -> bytes:
    """Hash the current journal bytes and physical ranges. A middle edit also invalidates."""
    server_hash = db.get_bind().dialect.name == "postgresql"

    def payload(column: Any) -> ColumnElement[Any]:
        raw = cast(column, Text)
        if server_hash:
            # Built into PostgreSQL 16. Send each payload's digest over the DB connection.
            return func.encode(func.sha256(func.convert_to(raw, "UTF8")), "hex")
        return raw

    record, chunk = MessageEvent, MessageEventChunk
    scoped = (record.conversation_id == room, record.assistant_msg_id == str(run))
    head = select(
        literal(0).label("kind"),
        cast(record.id, String).label("id"),
        literal(0).label("first"),
        literal(0).label("last"),
        record.status.label("witness"),
        payload(record.events).label("payload"),
    ).where(*scoped)
    parts = (
        select(
            literal(1),
            cast(chunk.id, String),
            chunk.seq_start,
            chunk.seq_end,
            payload(chunk.event_ids),
            payload(chunk.events),
        )
        .join(record, record.id == chunk.message_event_id)
        .where(*scoped, chunk.conversation_id == room, chunk.assistant_msg_id == str(run))
    )
    query = union_all(head, parts).order_by("kind", "first", "id")
    digest = sha256()
    rows = await db.stream(query.execution_options(yield_per=32, autoflush=False))
    try:
        async for row in rows:
            digest.update(orjson.dumps(tuple(row)))
    finally:
        await rows.close()
    return digest.digest()
