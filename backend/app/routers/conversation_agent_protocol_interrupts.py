from __future__ import annotations

import uuid
from typing import Any, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.langgraph_pending_inputs import interrupt_payloads_from_checkpointer
from app.agent_runtime.protocol_events import StoredProtocolEvent, protocol_interrupts_from_event
from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.routers.conversation_agent_protocol_replay import load_protocol_events
from app.routers.conversation_resume_decisions import STALE, decision_problem
from app.services import conversation_run_service


class ThreadInterrupt(TypedDict):
    id: str
    value: Any
    ns: list[str]


class ThreadTask(TypedDict):
    id: str
    name: str
    interrupts: list[ThreadInterrupt]


def _pending_interrupts_for_events(events: list[StoredProtocolEvent]) -> list[ThreadInterrupt]:
    by_id: dict[str, ThreadInterrupt] = {}
    order: list[str] = []
    for event in events:
        for interrupt in protocol_interrupts_from_event(event):
            thread_interrupt: ThreadInterrupt = {
                "id": interrupt["id"],
                "value": interrupt["value"],
                "ns": interrupt["ns"],
            }
            if interrupt["id"] not in by_id:
                order.append(interrupt["id"])
            by_id[interrupt["id"]] = thread_interrupt
    return [by_id[interrupt_id] for interrupt_id in order]


async def _checkpoint_interrupts(conversation: Conversation) -> list[ThreadInterrupt]:
    config = {"configurable": {"thread_id": str(conversation.id)}}
    return [
        {"id": p["interrupt_id"], "value": p["payload"], "ns": p["namespace"]}
        for p in await interrupt_payloads_from_checkpointer(config)
    ]


async def load_pending_interrupt_tasks(
    db: AsyncSession,
    conversation: Conversation,
    *,
    user_id: uuid.UUID,
    context: OrgContext,
) -> list[ThreadTask]:
    run = await conversation_run_service.get_latest_interrupted_run(
        db,
        conversation_id=conversation.id,
        context=context,
    )
    if run is None:
        return []

    run_id = str(run.id)
    events = [
        event
        for event in await load_protocol_events(db, conversation.id, context=context)
        if event["run_id"] == run_id
    ]
    interrupts: list[ThreadInterrupt] = _pending_interrupts_for_events(events)
    if not interrupts:
        interrupts = await _checkpoint_interrupts(conversation)
    if not interrupts:
        return []
    task: ThreadTask = {"id": run_id, "name": "interrupted", "interrupts": interrupts}
    return [task]


def interrupts_from_tasks(tasks: list[ThreadTask]) -> list[ThreadInterrupt]:
    return [interrupt for task in tasks for interrupt in task["interrupts"]]


async def require_allowed_rest_resume(
    db: AsyncSession,
    conversation: Conversation,
    context: OrgContext,
    interrupt_id: str | None,
    response: Any,
) -> None:
    """``POST /messages/resume`` (run or legacy trace): the G-06 check as a typed ``AppError``."""

    user_id = context.user_id
    tasks = await load_pending_interrupt_tasks(db, conversation, user_id=user_id, context=context)
    # A legacy trace resume has no interrupted run row: read the checkpoint directly.
    pending = interrupts_from_tasks(tasks) or await _checkpoint_interrupts(conversation)
    if not pending:
        return
    target = next((i for i in pending if i["id"] == interrupt_id), None)
    if target is None and len(pending) == 1:
        target = pending[0]
    if target is None:
        raise AppError(STALE, "resume does not name the pending interrupt", status=409)
    problem = decision_problem(response, target["value"])
    if problem is not None:
        code, message, status = problem
        raise AppError(code, message, status=status)
