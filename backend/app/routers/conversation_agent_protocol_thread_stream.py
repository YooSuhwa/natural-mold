from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable

from sqlalchemy.exc import SQLAlchemyError

from app.agent_runtime.event_broker import EventBroker
from app.agent_runtime.event_broker import registry as broker_registry
from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    SubscribeParams,
    format_protocol_sse,
    matches_subscription,
)
from app.database import async_session
from app.routers.conversation_agent_protocol_replay import (
    _events_after_cursor,
    load_protocol_events,
)
from app.routers.conversation_agent_protocol_runtime import protocol_event_from_broker

_THREAD_STREAM_POLL_SECONDS = 0.05
_THREAD_STREAM_REPLAY_POLL_SECONDS = 1.0
_THREAD_STREAM_HEARTBEAT_SECONDS = 10.0
_THREAD_STREAM_CHANNELS = {"lifecycle", "input"}
logger = logging.getLogger(__name__)


def needs_thread_stream(params: SubscribeParams) -> bool:
    channels = params.get("channels")
    return channels is None or any(channel in _THREAD_STREAM_CHANNELS for channel in channels)


def _event_cursor(event: StoredProtocolEvent) -> str:
    return event["upstream_event_id"] or str(event["seq"])


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


async def _yield_replay_events(
    *,
    conversation_id: uuid.UUID,
    params: SubscribeParams,
    cursor: str | None,
) -> AsyncGenerator[tuple[str, str], None]:
    events = await _load_replay_events(conversation_id)
    for event in _events_after_cursor(events, cursor):
        if not matches_subscription(event, params):
            continue
        yield format_protocol_sse(event), _event_cursor(event)


async def _yield_replay_events_or_skip(
    *,
    conversation_id: uuid.UUID,
    params: SubscribeParams,
    cursor: str | None,
) -> AsyncGenerator[tuple[str, str], None]:
    try:
        async for chunk, next_cursor in _yield_replay_events(
            conversation_id=conversation_id,
            params=params,
            cursor=cursor,
        ):
            yield chunk, next_cursor
    except SQLAlchemyError as exc:
        logger.debug("Skipping protocol thread replay after database read failed: %s", exc)


async def _yield_broker_events(
    *,
    broker: EventBroker,
    thread_id: str,
    params: SubscribeParams,
    cursor: str | None,
) -> AsyncGenerator[tuple[str, str], None]:
    broker_cursor = cursor if broker.has_event_id(cursor) else None
    async for event in broker.subscribe(after_id=broker_cursor):
        protocol_event = protocol_event_from_broker(
            event,
            run_id=broker.run_id,
            thread_id=thread_id,
        )
        if protocol_event is None or not matches_subscription(protocol_event, params):
            continue
        yield format_protocol_sse(protocol_event), _event_cursor(protocol_event)


async def protocol_thread_stream_generator(
    *,
    conversation_id: uuid.UUID,
    thread_id: str,
    params: SubscribeParams,
    after_id: str | None,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncGenerator[str, None]:
    cursor = after_id
    loop = asyncio.get_running_loop()
    next_replay_at = loop.time() + _THREAD_STREAM_REPLAY_POLL_SECONDS
    next_heartbeat_at = loop.time() + _THREAD_STREAM_HEARTBEAT_SECONDS

    async for chunk, next_cursor in _yield_replay_events_or_skip(
        conversation_id=conversation_id,
        params=params,
        cursor=cursor,
    ):
        cursor = next_cursor
        yield chunk

    while not await is_disconnected():
        broker = _latest_live_broker(conversation_id)
        if broker is not None:
            async for chunk, next_cursor in _yield_broker_events(
                broker=broker,
                thread_id=thread_id,
                params=params,
                cursor=cursor,
            ):
                cursor = next_cursor
                yield chunk
            next_replay_at = asyncio.get_running_loop().time()
            continue

        now = asyncio.get_running_loop().time()
        if now >= next_replay_at:
            replayed = False
            async for chunk, next_cursor in _yield_replay_events_or_skip(
                conversation_id=conversation_id,
                params=params,
                cursor=cursor,
            ):
                cursor = next_cursor
                replayed = True
                yield chunk
            next_replay_at = asyncio.get_running_loop().time() + _THREAD_STREAM_REPLAY_POLL_SECONDS
            if replayed:
                continue

        now = asyncio.get_running_loop().time()
        if now >= next_heartbeat_at:
            next_heartbeat_at = now + _THREAD_STREAM_HEARTBEAT_SECONDS
            yield ": heartbeat\n\n"
        await asyncio.sleep(_THREAD_STREAM_POLL_SECONDS)
