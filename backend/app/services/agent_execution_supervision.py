"""Pre-start cancel ack and cancel/heartbeat polling for owned executions.

Split from ``agent_execution_runtime`` (at its size cap); the facade re-exports
both names. Collaborators that tests patch on the facade (``async_session``,
``get_run_task_registry``) are read from it at call time.

``poll_cancel`` (I24 H2/M4): a ``canceling`` row owned by this worker, or a row
that became terminal/inactive elsewhere (archive cascade, another worker's
stale sweep) cancels the local task, unless the owner itself is already
writing its terminal (``OwnerFlags.finishing``). With ``heartbeat=True``
(prepared API/trigger runs) the poll also renews ``heartbeat_at`` on the
chat heartbeat cadence so another worker's sweep never fails a live run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import anyio
from sqlalchemy import select
from sqlalchemy.orm import load_only, noload

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import RUN_TERMINAL_STATUSES, ConversationRun
from app.services import conversation_run_service


@dataclass(slots=True)
class OwnerFlags:
    """Mutable owner state shared by the owner body and its poll task."""

    finishing: bool = False


def _owned() -> Any:
    from app.services import agent_execution_runtime

    return agent_execution_runtime


async def ack_prestart_cancel(request: Any) -> None:
    async with _owned().async_session() as db:
        run = await db.scalar(
            select(ConversationRun)
            .join(Conversation, Conversation.id == ConversationRun.conversation_id)
            .join(Agent, Agent.id == ConversationRun.agent_id)
            .options(
                load_only(
                    ConversationRun.id,
                    ConversationRun.conversation_id,
                    ConversationRun.agent_id,
                    ConversationRun.user_id,
                    ConversationRun.status,
                    ConversationRun.is_active,
                    ConversationRun.worker_instance_id,
                    ConversationRun.cancel_requested_at,
                    ConversationRun.cancellation_acknowledged_at,
                    ConversationRun.completed_at,
                    raiseload=True,
                ),
                noload(ConversationRun.metrics),
            )
            .where(
                ConversationRun.id == request.run_id,
                ConversationRun.conversation_id == request.conversation_id,
                ConversationRun.agent_id == request.root_agent_id,
                ConversationRun.user_id == request.context.user_id,
                ConversationRun.status == "canceling",
                ConversationRun.worker_instance_id.is_(None),
                Conversation.id == request.conversation_id,
                Conversation.agent_id == request.root_agent_id,
                Conversation.user_id == request.context.user_id,
                Conversation.org_id == request.context.org_id,
                Conversation.tenant_id == request.context.tenant_id,
                Agent.id == request.root_agent_id,
                Agent.user_id == request.context.user_id,
                Agent.org_id == request.context.org_id,
                Agent.tenant_id == request.context.tenant_id,
            )
            .with_for_update(of=ConversationRun)
            .execution_options(populate_existing=True, autoflush=False)
        )
        if run is None:
            return
        await conversation_run_service.transition_run(
            db,
            run,
            "canceled",
            allow_workerless_cancellation_ack=True,
        )
        await db.commit()


async def _heartbeat(run_id: Any) -> None:
    async with _owned().async_session() as db:
        await conversation_run_service.heartbeat_run(db, run_id)
        await db.commit()


def _interval() -> float:
    from app.services.conversation_run_worker import _heartbeat_interval_seconds

    return _heartbeat_interval_seconds()


async def poll_cancel(runtime: Any, heartbeat: bool = False) -> None:
    owned = _owned()
    registry = owned.get_run_task_registry()
    run_id = runtime.request.run_id
    next_beat = time.monotonic() + _interval()
    while True:
        await anyio.sleep(owned._CANCEL_POLL_SECONDS)  # noqa: SLF001
        async with owned.async_session() as db:
            row = await db.execute(
                select(ConversationRun)
                .options(
                    load_only(
                        ConversationRun.id,
                        ConversationRun.status,
                        ConversationRun.is_active,
                        ConversationRun.worker_instance_id,
                        raiseload=True,
                    ),
                    noload(ConversationRun.metrics),
                )
                .where(ConversationRun.id == run_id)
            )
            run = row.scalar_one_or_none()
        if run is None:
            return
        if run.status in RUN_TERMINAL_STATUSES or not run.is_active:
            if not runtime.flags.finishing:
                registry.request_cancel(run_id, reason="stop")
            return
        if run.status == "canceling" and run.worker_instance_id == runtime.worker_instance_id:
            registry.request_cancel(run.id, reason="stop")
            return
        if heartbeat and time.monotonic() >= next_beat:
            await _heartbeat(run_id)
            next_beat = time.monotonic() + _interval()


__all__ = ["OwnerFlags", "ack_prestart_cancel", "poll_cancel"]
