"""Read the committed source range for one authorized physical run.

The source position is one-based within the durable turn. SDK sequence numbers and
broker watermarks can advance before a flush and are not journal positions.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import StoredProtocolEvent
from app.models.conversation import Conversation
from app.models.conversation_history import ConversationHistoryEvent, ConversationHistoryManifest
from app.models.conversation_run import RUN_TERMINAL_STATUSES
from app.models.message_event import MessageEvent, MessageEventChunk
from app.schemas.conversation_history import JsonValue
from app.services.ag_ui_journal_legacy import legacy_journal_events
from app.services.ag_ui_snapshot_messages import legacy_user_messages
from app.services.conversation_history_projection import canonical_payload_sha256
from app.services.conversation_protocol_event_projection import (
    RawEventProjectionRequest,
    stored_events_from_raw,
)

_MAX_PAGE = 256
_CHUNKS_PER_PAGE = 4


@dataclass(frozen=True, slots=True)
class JournalEntry:
    ordinal: int
    source_id: str
    events: tuple[StoredProtocolEvent, ...]
    legacy_users: tuple[dict[str, Any], ...] = ()
    terminal_status: str | None = None


EntryProjector = Callable[
    [MessageEvent, dict[str, JsonValue], int, Collection[str]], JournalEntry | None
]


@dataclass(frozen=True, slots=True)
class JournalPage:
    generation: str | None
    entries: tuple[JournalEntry, ...]
    has_more: bool
    gap: bool
    trace_status: str | None
    high_water: int = 0


@dataclass(frozen=True, slots=True)
class _Range:
    first: int
    last: int
    events: Sequence[JsonValue]


def _entry(
    record: MessageEvent,
    raw: JsonValue,
    ordinal: int,
    secrets: Collection[str],
    legacy_projector: EntryProjector | None,
) -> JournalEntry | None:
    if not isinstance(raw, dict):
        return None
    source_id = raw.get("id") or raw.get("event_id")
    if not isinstance(source_id, str) or not source_id:
        source_id = f"{record.assistant_msg_id}:{ordinal}"
    terminal = None
    if "event" in raw and "method" not in raw:
        # Older emitters stored root SSE, without v3's first-class namespace.
        # Never relabel a child or another conversation's output as root text.
        data = raw.get("data")
        if not isinstance(data, dict) or any(
            envelope.get("run_id") not in (None, record.assistant_msg_id)
            or envelope.get("thread_id") not in (None, str(record.conversation_id))
            or envelope.get("conversation_id") not in (None, str(record.conversation_id))
            or envelope.get("namespace") not in (None, [], "")
            or envelope.get("checkpoint_ns") not in (None, "")
            for envelope in (raw, data)
        ):
            return None
        # The original emitter used one-based <physical-run>-<seq>. Its
        # best-effort writer could persist later flushes before a failed batch.
        prefix = f"{record.assistant_msg_id}-"
        if source_id.startswith(prefix):
            seq = source_id[len(prefix) :]
            if seq.isascii() and seq.isdecimal() and seq != str(ordinal):
                return None
        if legacy_projector is not None:
            return legacy_projector(record, raw, ordinal, secrets)
        if raw["event"] == "message_end":
            terminal = data.get("status", "completed")
        events = legacy_journal_events(record, raw, ordinal, secrets)
    else:
        # Never reattribute data that claims to belong to a different execution.
        if legacy_projector is not None:
            return None
        params = raw.get("params") if raw.get("type") == "event" else None
        # Wire-format events keep their namespace and payload together in params;
        # accepting a hybrid lets the compatibility reader pick a different root.
        if raw.get("type") == "event" and (not isinstance(params, dict) or "data" in raw):
            return None
        envelopes = (raw, params) if isinstance(params, dict) else (raw,)
        scopes = [env["namespace"] for env in envelopes if "namespace" in env]
        if (
            any(
                env.get("run_id") not in (None, record.assistant_msg_id)
                or env.get("thread_id") not in (None, str(record.conversation_id))
                or env.get("conversation_id") not in (None, str(record.conversation_id))
                or (
                    env.get("checkpoint_ns") is not None
                    and not isinstance(env["checkpoint_ns"], str)
                )
                for env in envelopes
            )
            or any(
                not isinstance(ns, list) or not all(isinstance(x, str) and x for x in ns)
                for ns in scopes
            )
            or isinstance(params, dict)
            and (
                "namespace" in raw
                and raw["namespace"] != params.get("namespace", [])
                or "checkpoint_ns" in raw
                and raw["checkpoint_ns"] != params.get("checkpoint_ns")
            )
        ):
            return None
        try:
            events = tuple(
                stored_events_from_raw(RawEventProjectionRequest(raw, record, ordinal, secrets))
            )
        except (TypeError, ValueError):
            return None
        if not events:
            return None
        for event in events:
            payload = event["data"]
            if (
                event["method"] == "lifecycle"
                and not event["namespace"]
                and not event["checkpoint_ns"]
                and isinstance(payload, dict)
            ):
                terminal = payload.get("event")
    if events is None:
        return None
    users = (
        legacy_user_messages(
            raw,
            conversation_id=record.conversation_id,
            physical_run_id=record.assistant_msg_id,
            secret_values=secrets,
        )
        if raw.get("event") == "message_start"
        else []
    )
    return JournalEntry(
        ordinal,
        source_id,
        events,
        tuple(users),
        terminal if isinstance(terminal, str) and terminal in RUN_TERMINAL_STATUSES else None,
    )


def _page(
    record: MessageEvent,
    generation: str,
    status: str,
    ranges: Sequence[_Range],
    *,
    after: int,
    limit: int,
    high_water: int,
    secrets: Collection[str],
    legacy_projector: EntryProjector | None = None,
) -> JournalPage:
    gap = JournalPage(generation, (), False, True, status, high_water)
    if after > high_water:
        return gap
    if after == high_water:
        return JournalPage(generation, (), False, False, status, high_water)
    next_pos = after + 1
    entries: list[JournalEntry] = []
    for index, group in enumerate(ranges):
        if (
            group.first < 1
            or group.last - group.first + 1 != len(group.events)
            or group.last > high_water
            or (index and group.first != ranges[index - 1].last + 1)
            or (not index and not group.first <= next_pos <= group.last)
        ):
            return gap
        stop = min(group.last + 1, after + limit + 1)
        for ordinal in range(next_pos, stop):
            entry = _entry(
                record, group.events[ordinal - group.first], ordinal, secrets, legacy_projector
            )
            if entry is None:
                return gap
            entries.append(entry)
        next_pos = max(next_pos, stop)
    if not entries:
        return gap
    return JournalPage(
        generation, tuple(entries), entries[-1].ordinal < high_water, False, status, high_water
    )


async def read_journal_page(
    db: AsyncSession,
    *,
    conversation: Conversation,
    physical_run_id: UUID,
    after: int = 0,
    limit: int = 128,
    secret_values: Collection[str] = (),
) -> JournalPage:
    """Return only committed source rows. The caller reauthorizes before each page."""
    if after < 0 or not 1 <= limit <= _MAX_PAGE:
        raise ValueError("invalid journal position or page size")
    # The synthetic model is used only by the existing protocol projection to
    # supply its trusted run/conversation ids. It is never added to the session.
    record = MessageEvent(conversation_id=conversation.id, assistant_msg_id=str(physical_run_id))
    with db.no_autoflush:
        if conversation.agent_lifecycle_state == "purged":
            return await _retained_page(db, record, after, limit, secret_values)
        return await _live_page(db, record, after, limit, secret_values)


async def read_legacy_trace_page(
    db: AsyncSession,
    *,
    conversation: Conversation,
    physical_run_id: UUID,
    entry_projector: EntryProjector,
    after: int = 0,
    limit: int = 128,
    secret_values: Collection[str] = (),
) -> JournalPage:
    """Read a tenant-authorized historical transcript without a ConversationRun row."""
    if after < 0 or not 1 <= limit <= _MAX_PAGE:
        raise ValueError("invalid journal position or page size")
    if conversation.agent_lifecycle_state == "purged":
        return JournalPage(None, (), False, True, None)
    record = MessageEvent(conversation_id=conversation.id, assistant_msg_id=str(physical_run_id))
    with db.no_autoflush:
        return await _live_page(db, record, after, limit, secret_values, entry_projector)


async def _live_page(
    db: AsyncSession,
    record: MessageEvent,
    after: int,
    limit: int,
    secrets: Collection[str],
    legacy_projector: EntryProjector | None = None,
) -> JournalPage:
    chunk = MessageEventChunk
    run_scope = (
        chunk.conversation_id == record.conversation_id,
        chunk.assistant_msg_id == record.assistant_msg_id,
    )
    end = (
        select(chunk.seq_end)
        .where(chunk.message_event_id == MessageEvent.id, *run_scope)
        .order_by(chunk.seq_start.desc())
        .limit(1)
        .scalar_subquery()
    )
    size = func.json_array_length(MessageEvent.events)
    # The horizon, status and prefix length come from one database snapshot. An
    # append committed during this read is eligible only in the next poll.
    row = (
        await db.execute(
            select(
                MessageEvent.id,
                MessageEvent.status,
                size,
                case((size > after, MessageEvent.events), else_=None),
                end,
            ).where(
                MessageEvent.conversation_id == record.conversation_id,
                MessageEvent.assistant_msg_id == record.assistant_msg_id,
            )
        )
    ).one_or_none()
    if row is None:
        return JournalPage(None, (), False, after != 0, None)
    record_id, status, prefix_size, prefix, tail = row
    generation = str(record_id)
    horizon = max(prefix_size, tail or 0)
    ranges: list[_Range] = []
    if prefix_size > after:
        ranges.append(_Range(1, prefix_size, prefix))
    if horizon > max(prefix_size, after) and after + limit > prefix_size:
        parts = await db.execute(
            select(chunk.seq_start, chunk.seq_end, chunk.events, chunk.event_ids)
            .where(
                chunk.message_event_id == record_id,
                *run_scope,
                chunk.seq_end > max(after, prefix_size),
                chunk.seq_start <= min(horizon, after + limit),
            )
            .order_by(chunk.seq_start, chunk.created_at, chunk.id)
            .limit(_CHUNKS_PER_PAGE)
        )
        for first, last, events, ids in parts:
            if (
                first <= prefix_size
                or not isinstance(events, list)
                or ids != [raw["id"] for raw in events if isinstance(raw, dict) and raw.get("id")]
            ):
                return JournalPage(generation, (), False, True, status, horizon)
            ranges.append(_Range(first, last, events))
    return _page(
        record,
        generation,
        status,
        ranges,
        after=after,
        limit=limit,
        high_water=horizon,
        secrets=secrets,
        legacy_projector=legacy_projector,
    )


async def _retained_page(
    db: AsyncSession, record: MessageEvent, after: int, limit: int, secrets: Collection[str]
) -> JournalPage:
    event = ConversationHistoryEvent
    manifest = ConversationHistoryManifest
    run_scope = (
        event.conversation_id == record.conversation_id,
        event.assistant_message_id == record.assistant_msg_id,
    )
    end = (
        select(func.max(event.sequence_end))
        .where(
            event.manifest_id == manifest.id, *run_scope, func.json_array_length(event.events) > 0
        )
        .scalar_subquery()
    )
    head = (
        await db.execute(
            select(manifest.id, end).where(
                manifest.conversation_id == record.conversation_id,
                manifest.schema_version == 1,
                manifest.state == "complete",
            )
        )
    ).one_or_none()
    if head is None:
        return JournalPage(None, (), False, True, None)
    manifest_id, last_index = head
    generation = str(manifest_id)
    horizon = last_index + 1 if last_index is not None else 0
    ranges: list[_Range] = []
    if horizon > after:
        parts = await db.execute(
            select(event.sequence_start, event.sequence_end, event.events, event.payload_sha256)
            .where(
                event.manifest_id == manifest_id,
                *run_scope,
                event.sequence_end >= after,
                event.sequence_start < min(after + limit, horizon),
            )
            .order_by(event.sequence_start, event.id)
            .limit(_CHUNKS_PER_PAGE)
        )
        for start, end_index, events, checksum in parts:
            if not isinstance(events, list) or canonical_payload_sha256(events) != checksum:
                return JournalPage(generation, (), False, True, "completed", horizon)
            ranges.append(_Range(start + 1, end_index + 1, events))
    return _page(
        record,
        generation,
        "completed",
        ranges,
        after=after,
        limit=limit,
        high_water=horizon,
        secrets=secrets,
    )
