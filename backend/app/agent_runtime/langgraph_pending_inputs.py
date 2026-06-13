from __future__ import annotations

from typing import Any

from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    canonical_input_requested_events,
    protocol_interrupts_from_event,
    stored_protocol_event,
)
from app.agent_runtime.streaming import _interrupt_to_standard_chunk


def _next_protocol_seq(events: list[dict[str, Any]]) -> int:
    seq_values = [event.get("seq") for event in events if isinstance(event.get("seq"), int)]
    return max(seq_values, default=0) + 1


def _seen_interrupt_ids(events: list[dict[str, Any]]) -> set[str]:
    seen: set[str] = set()
    for event in events:
        for interrupt in protocol_interrupts_from_event(event):
            seen.add(interrupt["id"])
    return seen


def _interrupt_payloads_from_state(state: Any) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    interrupts = list(getattr(state, "interrupts", ()) or ())
    if not interrupts:
        for task in getattr(state, "tasks", ()) or ():
            interrupts.extend(getattr(task, "interrupts", ()) or ())
    for index, interrupt in enumerate(interrupts):
        intr_id = str(getattr(interrupt, "id", "") or f"interrupt-{index + 1}")
        intr_value = getattr(interrupt, "value", None)
        standard = _interrupt_to_standard_chunk(
            intr_id,
            intr_value if isinstance(intr_value, dict) else None,
        )
        if standard is not None:
            payloads.append(standard)
    return payloads


async def pending_input_requested_events(
    agent: Any,
    config: dict[str, Any],
    *,
    run_id: str,
    thread_id: str,
    emitted: list[dict[str, Any]],
) -> list[StoredProtocolEvent]:
    try:
        state = await agent.aget_state(config)
    except Exception:
        return []

    seen_interrupt_ids = _seen_interrupt_ids(emitted)
    raw_interrupts: list[dict[str, Any]] = []
    for payload in _interrupt_payloads_from_state(state):
        interrupt_id = str(payload.get("interrupt_id") or "")
        if not interrupt_id or interrupt_id in seen_interrupt_ids:
            continue
        raw_interrupts.append({"id": interrupt_id, "value": payload})

    if not raw_interrupts:
        return []

    source_event = stored_protocol_event(
        run_id=run_id,
        thread_id=thread_id,
        seq=_next_protocol_seq(emitted),
        method="values",
        data={"__interrupt__": raw_interrupts},
    )
    return canonical_input_requested_events(source_event)
