from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypedDict

from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    canonical_input_requested_events,
    protocol_interrupts_from_event,
    stored_protocol_event,
)
from app.agent_runtime.streaming import _interrupt_to_standard_chunk


class PendingInputPayload(TypedDict):
    interrupt_id: str
    payload: dict[str, Any]
    namespace: list[str]


def _next_protocol_seq(events: list[dict[str, Any]]) -> int:
    seq_values = [event.get("seq") for event in events if isinstance(event.get("seq"), int)]
    return max(seq_values, default=0) + 1


def _seen_interrupt_ids(events: list[dict[str, Any]]) -> set[str]:
    seen: set[str] = set()
    for event in events:
        for interrupt in protocol_interrupts_from_event(event):
            seen.add(interrupt["id"])
    return seen


def _namespace_from_path_segment(segment: Any) -> list[str]:
    if isinstance(segment, str):
        return [segment] if ":" in segment and not segment.startswith("__") else []
    if isinstance(segment, Sequence) and not isinstance(segment, str | bytes):
        namespace: list[str] = []
        for item in segment:
            namespace.extend(_namespace_from_path_segment(item))
        return namespace
    return []


def _namespace_from_task(task: Any) -> list[str]:
    path = getattr(task, "path", ())
    if not isinstance(path, Sequence) or isinstance(path, str | bytes):
        return []
    namespace: list[str] = []
    for segment in path:
        namespace.extend(_namespace_from_path_segment(segment))
    return namespace


def _payload_from_interrupt(
    interrupt: Any,
    *,
    namespace: list[str],
    index: int,
) -> PendingInputPayload | None:
    intr_id = str(getattr(interrupt, "id", "") or f"interrupt-{index + 1}")
    intr_value = getattr(interrupt, "value", None)
    standard = _interrupt_to_standard_chunk(
        intr_id,
        intr_value if isinstance(intr_value, dict) else None,
    )
    if standard is None:
        return None
    return {
        "interrupt_id": intr_id,
        "payload": standard,
        "namespace": namespace,
    }


def _interrupt_payloads_from_state(state: Any) -> list[PendingInputPayload]:
    payloads: list[PendingInputPayload] = []
    interrupts = list(getattr(state, "interrupts", ()) or ())
    for index, interrupt in enumerate(interrupts):
        payload = _payload_from_interrupt(interrupt, namespace=[], index=index)
        if payload is not None:
            payloads.append(payload)
    if payloads:
        return payloads
    for task in getattr(state, "tasks", ()) or ():
        namespace = _namespace_from_task(task)
        for index, interrupt in enumerate(getattr(task, "interrupts", ()) or ()):
            payload = _payload_from_interrupt(interrupt, namespace=namespace, index=index)
            if payload is not None:
                payloads.append(payload)
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
        interrupt_id = payload["interrupt_id"]
        if not interrupt_id or interrupt_id in seen_interrupt_ids:
            continue
        raw_interrupts.append(
            {
                "id": interrupt_id,
                "value": payload["payload"],
                "ns": payload["namespace"],
            }
        )

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
