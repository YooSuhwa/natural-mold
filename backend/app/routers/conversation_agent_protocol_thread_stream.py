from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from contextlib import aclosing

from sqlalchemy.exc import SQLAlchemyError

from app.agent_runtime.event_broker import EventBroker, broker_for_run_owner
from app.agent_runtime.event_broker import registry as broker_registry
from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    SubscribeParams,
    format_protocol_sse,
    matches_subscription,
    protocol_event_cursor,
    resequence_protocol_event,
)
from app.authz.conversation_scope import resolve_conversation_actor
from app.authz.org_context import OrgContext
from app.database import tenant_session
from app.error_codes import conversation_not_found
from app.routers.conversation_agent_protocol_attach import broker_events_while_owned
from app.routers.conversation_agent_protocol_replay import (
    _events_after_cursor,
    load_protocol_events,
)
from app.routers.conversation_agent_protocol_runtime import protocol_events_from_broker
from app.services import conversation_run_service, conversation_stream_service

_THREAD_STREAM_POLL_SECONDS = 0.05
_THREAD_STREAM_REPLAY_POLL_SECONDS = 1.0
_THREAD_STREAM_REPLAY_MAX_SECONDS = 30.0
_THREAD_STREAM_HEARTBEAT_SECONDS = 10.0
_THREAD_STREAM_OWNER_POLL_SECONDS = 1.0
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


async def _broker_for_current_owner(
    conversation_id: uuid.UUID, context: OrgContext, candidate: EventBroker
) -> EventBroker | None:
    # A dead worker may keep its broker open after another process has taken
    # over the run. A registry hit is a wake-up signal, never proof of ownership.
    try:
        async with tenant_session(context, maker=conversation_stream_service.async_session) as db:
            fresh = await resolve_conversation_actor(db, conversation_id, context.user_id)
            if (fresh.org_id, fresh.tenant_id) != (context.org_id, context.tenant_id):
                raise conversation_not_found()
            run = await conversation_run_service.get_active_run(
                db, conversation_id=conversation_id, context=fresh
            )
            if run is None:
                # Legacy local streams may exist without a durable execution row.
                if broker_for_run_owner(candidate, 0) is None:
                    return None
                try:
                    candidate_id = uuid.UUID(candidate.run_id)
                except ValueError:
                    return candidate
                recorded = await conversation_run_service.get_run_for_user(
                    db, conversation_id=conversation_id, run_id=candidate_id, context=fresh
                )
                return candidate if recorded is None else None
    except SQLAlchemyError as exc:
        logger.debug("Could not verify the thread broker owner: %s", exc)
        return None
    broker = broker_for_run_owner(broker_registry.get(str(run.id)), run.owner_generation)
    if broker is None or broker.is_closed or broker.conversation_id != str(conversation_id):
        return None
    return broker


async def _load_replay_events(
    conversation_id: uuid.UUID, context: OrgContext
) -> list[StoredProtocolEvent]:
    async with tenant_session(context, maker=conversation_stream_service.async_session) as session:
        fresh = await resolve_conversation_actor(session, conversation_id, context.user_id)
        if (fresh.org_id, fresh.tenant_id) != (context.org_id, context.tenant_id):
            raise conversation_not_found()
        return await load_protocol_events(session, conversation_id, context=fresh)


def _numeric_since(params: SubscribeParams) -> int:
    since = params.get("since")
    if isinstance(since, int):
        return since
    if isinstance(since, str) and since.isdigit():
        return int(since)
    return 0


def _max_event_seq(events: list[StoredProtocolEvent]) -> int:
    return max((event["seq"] for event in events), default=0)


def _next_replay_delay(current_delay: float) -> float:
    return min(current_delay * 2, _THREAD_STREAM_REPLAY_MAX_SECONDS)


async def _load_replay_events_or_empty(
    conversation_id: uuid.UUID,
    context: OrgContext,
) -> list[StoredProtocolEvent]:
    try:
        return await _load_replay_events(conversation_id, context)
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
    conversation_id: uuid.UUID,
    context: OrgContext,
) -> AsyncGenerator[tuple[str, str, int], None]:
    broker_cursor = cursor if broker.has_event_id(cursor) else None
    events = broker_events_while_owned(
        broker, after_id=broker_cursor, conversation_id=conversation_id, context=context
    )
    async with aclosing(events):
        async for event in events:
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
    context: OrgContext,
    thread_id: str,
    params: SubscribeParams,
    after_id: str | None,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncGenerator[str, None]:
    cursor = after_id
    last_seq = _numeric_since(params)
    loop = asyncio.get_running_loop()
    replay_delay = _THREAD_STREAM_REPLAY_POLL_SECONDS
    next_replay_at = loop.time() + replay_delay
    next_heartbeat_at = loop.time() + _THREAD_STREAM_HEARTBEAT_SECONDS

    replay_events = await _load_replay_events_or_empty(conversation_id, context)
    last_seq = max(last_seq, _max_event_seq(replay_events))
    for event in _iter_replay_events(replay_events, params=params, cursor=cursor):
        cursor = _event_cursor(event)
        yield format_protocol_sse(event)

    last_candidate: EventBroker | None = None
    next_owner_check = 0.0
    while not await is_disconnected():
        candidate = _latest_live_broker(conversation_id)
        now = loop.time()
        broker = None
        if candidate is not None and (candidate is not last_candidate or now >= next_owner_check):
            broker = await _broker_for_current_owner(conversation_id, context, candidate)
            next_owner_check = loop.time() + _THREAD_STREAM_OWNER_POLL_SECONDS
        last_candidate = candidate
        if broker is not None:
            events = _yield_broker_events(
                broker=broker,
                thread_id=thread_id,
                params=params,
                cursor=cursor,
                next_seq=last_seq,
                conversation_id=conversation_id,
                context=context,
            )
            async with aclosing(events):
                async for chunk, next_cursor, next_seq in events:
                    cursor = next_cursor
                    last_seq = next_seq
                    yield chunk
            replay_delay = _THREAD_STREAM_REPLAY_POLL_SECONDS
            next_replay_at = asyncio.get_running_loop().time()
            continue

        now = asyncio.get_running_loop().time()
        if now >= next_replay_at:
            replayed = False
            replay_events = await _load_replay_events_or_empty(conversation_id, context)
            last_seq = max(last_seq, _max_event_seq(replay_events))
            for event in _iter_replay_events(replay_events, params=params, cursor=cursor):
                cursor = _event_cursor(event)
                replayed = True
                yield format_protocol_sse(event)
            replay_delay = (
                _THREAD_STREAM_REPLAY_POLL_SECONDS if replayed else _next_replay_delay(replay_delay)
            )
            next_replay_at = asyncio.get_running_loop().time() + replay_delay
            if replayed:
                continue

        now = asyncio.get_running_loop().time()
        if now >= next_heartbeat_at:
            next_heartbeat_at = now + _THREAD_STREAM_HEARTBEAT_SECONDS
            yield ": heartbeat\n\n"
        await asyncio.sleep(_THREAD_STREAM_POLL_SECONDS)
