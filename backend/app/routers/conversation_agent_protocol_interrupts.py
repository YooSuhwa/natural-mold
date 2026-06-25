from __future__ import annotations

import uuid
from typing import Any, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.langgraph_pending_inputs import interrupt_payloads_from_checkpointer
from app.agent_runtime.protocol_events import StoredProtocolEvent, protocol_interrupts_from_event
from app.models.conversation import Conversation
from app.routers.conversation_agent_protocol_replay import load_protocol_events
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


async def load_pending_interrupt_tasks(
    db: AsyncSession,
    conversation: Conversation,
    *,
    user_id: uuid.UUID,
) -> list[ThreadTask]:
    run = await conversation_run_service.get_latest_interrupted_run(
        db,
        conversation_id=conversation.id,
        user_id=user_id,
    )
    if run is None:
        return []

    run_id = str(run.id)
    events = [
        event
        for event in await load_protocol_events(db, conversation.id)
        if event["run_id"] == run_id
    ]
    interrupts = _pending_interrupts_for_events(events)
    if not interrupts:
        interrupts = [
            {
                "id": payload["interrupt_id"],
                "value": payload["payload"],
                "ns": payload["namespace"],
            }
            for payload in await interrupt_payloads_from_checkpointer(
                {"configurable": {"thread_id": str(conversation.id)}}
            )
        ]
    if not interrupts:
        return []
    return [{"id": run_id, "name": "interrupted", "interrupts": interrupts}]


def interrupts_from_tasks(tasks: list[ThreadTask]) -> list[ThreadInterrupt]:
    return [interrupt for task in tasks for interrupt in task["interrupts"]]
