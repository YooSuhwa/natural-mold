from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import StoredProtocolEvent
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


def _string_value(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _namespace(value: Any, fallback: list[str]) -> list[str]:
    if isinstance(value, list):
        return [segment for segment in value if isinstance(segment, str)]
    return list(fallback)


def _input_requested_interrupt(event: StoredProtocolEvent) -> ThreadInterrupt | None:
    if event["method"] != "input.requested" or not isinstance(event["data"], Mapping):
        return None
    data = event["data"]
    interrupt_id = _string_value(data.get("interrupt_id")) or _string_value(data.get("id"))
    if interrupt_id is None:
        return None
    value = data.get("payload") if "payload" in data else data.get("value")
    return {
        "id": interrupt_id,
        "value": value,
        "ns": _namespace(data.get("namespace") or data.get("ns"), event["namespace"]),
    }


def _state_interrupts(event: StoredProtocolEvent) -> list[ThreadInterrupt]:
    if event["method"] not in {"values", "updates"} or not isinstance(event["data"], Mapping):
        return []
    raw_interrupts = event["data"].get("__interrupt__")
    if not isinstance(raw_interrupts, list):
        return []

    interrupts: list[ThreadInterrupt] = []
    for raw in raw_interrupts:
        if not isinstance(raw, Mapping):
            continue
        interrupt_id = _string_value(raw.get("id")) or _string_value(raw.get("interrupt_id"))
        if interrupt_id is None:
            continue
        value = raw.get("value") if "value" in raw else raw.get("payload")
        interrupts.append(
            {
                "id": interrupt_id,
                "value": value,
                "ns": _namespace(raw.get("ns") or raw.get("namespace"), event["namespace"]),
            }
        )
    return interrupts


def _pending_interrupts_for_events(events: list[StoredProtocolEvent]) -> list[ThreadInterrupt]:
    by_id: dict[str, ThreadInterrupt] = {}
    order: list[str] = []
    for event in events:
        input_interrupt = _input_requested_interrupt(event)
        candidates = [input_interrupt] if input_interrupt is not None else _state_interrupts(event)
        for interrupt in candidates:
            if interrupt["id"] not in by_id:
                order.append(interrupt["id"])
            by_id[interrupt["id"]] = interrupt
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
        event for event in await load_protocol_events(db, conversation.id) if event["run_id"] == run_id
    ]
    interrupts = _pending_interrupts_for_events(events)
    if not interrupts:
        return []
    return [{"id": run_id, "name": "interrupted", "interrupts": interrupts}]


def interrupts_from_tasks(tasks: list[ThreadTask]) -> list[ThreadInterrupt]:
    return [interrupt for task in tasks for interrupt in task["interrupts"]]
