from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from contextlib import aclosing

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.event_broker import BrokeredEvent, EventBroker, broker_for_run_owner
from app.agent_runtime.event_broker import registry as broker_registry
from app.authz.org_context import OrgContext
from app.config import settings
from app.database import tenant_session
from app.exceptions import AppError
from app.models.conversation_run import ConversationRun
from app.services import conversation_run_service, conversation_stream_service

_RUN_ATTACH_SETTLE_SECONDS = 1.0
_RUN_ATTACH_SETTLE_INTERVAL_SECONDS = 0.05
_RUN_OWNER_POLL_SECONDS = 1.0


def live_broker_for_run(run: ConversationRun | None) -> EventBroker | None:
    if run is None or not run.is_active:
        return None
    broker = broker_for_run_owner(broker_registry.get(str(run.id)), run.owner_generation)
    if broker is None or broker.is_closed or broker.conversation_id != str(run.conversation_id):
        return None
    return broker


async def wait_for_live_broker_or_terminal(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> ConversationRun | None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _RUN_ATTACH_SETTLE_SECONDS
    while True:
        run = await db.get(ConversationRun, run_id, populate_existing=True)
        if run is None or not run.is_active or live_broker_for_run(run) is not None:
            return run
        remaining = deadline - loop.time()
        if remaining <= 0:
            return run
        await asyncio.sleep(min(_RUN_ATTACH_SETTLE_INTERVAL_SECONDS, remaining))


async def _broker_owner_state(
    broker: EventBroker,
    conversation_id: uuid.UUID,
    context: OrgContext,
) -> tuple[bool, bool]:
    """Whether this broker may emit, and whether its generation is permanently zero."""
    try:
        run_id = uuid.UUID(broker.run_id)
    except ValueError:
        return (broker.owner_generation == 0, True)
    try:
        async with tenant_session(context, maker=conversation_stream_service.async_session) as db:
            run = await conversation_run_service.get_run_for_user(
                db, conversation_id=conversation_id, run_id=run_id, context=context
            )
            if run is None:
                # Older conversation streams can have no durable run.
                return (broker.owner_generation == 0, True)
            matches = run.is_active and broker_for_run_owner(broker, run.owner_generation) is broker
            # Enrollment is atomic with queued -> running. An unarmed, running
            # execution cannot gain a recovery generation later.
            return matches, run.owner_generation == 0 and run.status != "queued"
    except (AppError, SQLAlchemyError):
        # A disconnected subscriber retries through the normal scoped read route.
        return False, False


async def while_broker_owner[T](
    source: AsyncGenerator[T, None],
    *,
    broker: EventBroker,
    conversation_id: uuid.UUID,
    context: OrgContext,
) -> AsyncGenerator[T, None]:
    """End a live response if another worker takes ownership, even during silence."""
    async with aclosing(source):
        if not broker.owner_generation and not settings.chat_run_recovery_enabled:
            async for item in source:
                yield item
            return

        pending: asyncio.Future[T] | None = None
        loop = asyncio.get_running_loop()
        next_check = loop.time()
        try:
            while True:
                if loop.time() >= next_check:
                    valid, fixed = await _broker_owner_state(broker, conversation_id, context)
                    if not valid:
                        return
                    if fixed:
                        if pending is not None:
                            yield await pending
                            pending = None
                        async for item in source:
                            yield item
                        return
                    next_check = loop.time() + _RUN_OWNER_POLL_SECONDS
                if pending is None:
                    pending = asyncio.ensure_future(anext(source))
                if not pending.done():
                    await asyncio.wait((pending,), timeout=max(0.0, next_check - loop.time()))
                    continue
                item = pending.result()
                pending = None
                yield item
        except StopAsyncIteration:
            return
        finally:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


def broker_events_while_owned(
    broker: EventBroker, *, after_id: str | None, conversation_id: uuid.UUID, context: OrgContext
) -> AsyncGenerator[BrokeredEvent, None]:
    return while_broker_owner(
        broker.subscribe(after_id=after_id),
        broker=broker,
        conversation_id=conversation_id,
        context=context,
    )
