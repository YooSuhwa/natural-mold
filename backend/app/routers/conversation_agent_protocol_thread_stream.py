from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator

from sqlalchemy.exc import SQLAlchemyError

from app.agent_runtime.event_broker import EventBroker
from app.agent_runtime.event_broker import registry as broker_registry
from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    SubscribeParams,
    format_protocol_sse,
    matches_subscription,
    protocol_event_cursor,
    resequence_protocol_event,
)
from app.database import async_session
from app.routers.conversation_agent_protocol_replay import (
    _events_after_cursor,
    load_protocol_events,
)
from app.routers.conversation_agent_protocol_runtime import protocol_events_from_broker

_THREAD_STREAM_POLL_SECONDS = 0.05
_THREAD_STREAM_REPLAY_POLL_SECONDS = 1.0
_THREAD_STREAM_HEARTBEAT_SECONDS = 10.0
_THREAD_STREAM_CHANNELS = {"lifecycle", "input"}
logger = logging.getLogger(__name__)


def needs_thread_stream(params: SubscribeParams) -> bool:
    channels = params.get("channels")
    return channels is None or any(channel in _THREAD_STREAM_CHANNELS for channel in channels)


def _event_cursor(event: StoredProtocolEvent) -> str:
    return protocol_event_cursor(event)


def _latest_live_broker(conversation_id: uuid.UUID) -> EventBroker | None:
    live_brokers = [
        broker
        for broker in broker_registry.all_brokers()
        if broker.conversation_id == str(conversation_id) and not broker.is_closed
    ]
    if not live_brokers:
        return None
    return max(live_brokers, key=lambda broker: broker.created_at)


async def _load_replay_events(conversation_id: uuid.UUID) -> list[StoredProtocolEvent]:
    async with async_session() as session:
        return await load_protocol_events(session, conversation_id)


def _numeric_since(params: SubscribeParams) -> int:
    since = params.get("since")
    if isinstance(since, int):
        return since
    if isinstance(since, str) and since.isdigit():
        return int(since)
    return 0


def _max_event_seq(events: list[StoredProtocolEvent]) -> int:
    return max((event["seq"] for event in events), default=0)


async def _load_replay_events_or_empty(
    conversation_id: uuid.UUID,
) -> list[StoredProtocolEvent]:
    try:
        return await _load_replay_events(conversation_id)
    except SQLAlchemyError as exc:
        logger.debug("Skipping protocol thread replay after database read failed: %s", exc)
        return []


def _iter_replay_events(
    events: list[StoredProtocolEvent],
    *,
    params: SubscribeParams,
    cursor: str | None,
) -> Iterator[StoredProtocolEvent]:
    for event in _events_after_cursor(events, cursor):
        if not matches_subscription(event, params):
            continue
        yield event


async def _yield_broker_events(
    *,
    broker: EventBroker,
    thread_id: str,
    params: SubscribeParams,
    cursor: str | None,
    next_seq: int,
) -> AsyncGenerator[tuple[str, str, int], None]:
    broker_cursor = cursor if broker.has_event_id(cursor) else None
    async for event in broker.subscribe(after_id=broker_cursor):
        broker_event_id = event.get("id")
        event_cursor = (
            broker_event_id if isinstance(broker_event_id, str) and broker_event_id else None
        )
        protocol_events = protocol_events_from_broker(
            event,
            run_id=broker.run_id,
            thread_id=thread_id,
        )
        for protocol_event in protocol_events:
            next_seq += 1
            projected_event = resequence_protocol_event(protocol_event, seq=next_seq)
            if not matches_subscription(projected_event, params):
                continue
            next_cursor = event_cursor or _event_cursor(projected_event)
            yield (
                format_protocol_sse(projected_event, cursor=next_cursor),
                next_cursor,
                next_seq,
            )


async def protocol_thread_stream_generator(
    *,
    conversation_id: uuid.UUID,
    thread_id: str,
    params: SubscribeParams,
    after_id: str | None,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncGenerator[str, None]:
    cursor = after_id
    last_seq = _numeric_since(params)
    loop = asyncio.get_running_loop()
    next_replay_at = loop.time() + _THREAD_STREAM_REPLAY_POLL_SECONDS
    next_heartbeat_at = loop.time() + _THREAD_STREAM_HEARTBEAT_SECONDS

    replay_events = await _load_replay_events_or_empty(conversation_id)
    last_seq = max(last_seq, _max_event_seq(replay_events))
    for event in _iter_replay_events(replay_events, params=params, cursor=cursor):
        cursor = _event_cursor(event)
        yield format_protocol_sse(event)

    while not await is_disconnected():
        broker = _latest_live_broker(conversation_id)
        if broker is not None:
            async for chunk, next_cursor, next_seq in _yield_broker_events(
                broker=broker,
                thread_id=thread_id,
                params=params,
                cursor=cursor,
                next_seq=last_seq,
            ):
                cursor = next_cursor
                last_seq = next_seq
                yield chunk
            next_replay_at = asyncio.get_running_loop().time()
            continue

        now = asyncio.get_running_loop().time()
        if now >= next_replay_at:
            replayed = False
            replay_events = await _load_replay_events_or_empty(conversation_id)
            last_seq = max(last_seq, _max_event_seq(replay_events))
            for event in _iter_replay_events(replay_events, params=params, cursor=cursor):
                cursor = _event_cursor(event)
                replayed = True
                yield format_protocol_sse(event)
            next_replay_at = asyncio.get_running_loop().time() + _THREAD_STREAM_REPLAY_POLL_SECONDS
            if replayed:
                continue

        now = asyncio.get_running_loop().time()
        if now >= next_heartbeat_at:
            next_heartbeat_at = now + _THREAD_STREAM_HEARTBEAT_SECONDS
            yield ": heartbeat\n\n"
        await asyncio.sleep(_THREAD_STREAM_POLL_SECONDS)
