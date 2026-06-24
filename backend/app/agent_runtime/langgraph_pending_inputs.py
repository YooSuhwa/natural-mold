from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    canonical_input_requested_events,
    protocol_interrupts_from_event,
    stored_protocol_event,
)
from app.agent_runtime.streaming import _interrupt_to_standard_chunk

logger = logging.getLogger(__name__)


class PendingInputPayload(TypedDict):
    interrupt_id: str
    payload: dict[str, Any]
    namespace: list[str]


class PendingInputStateUnavailable(RuntimeError):
    pass


def _next_protocol_seq(events: list[dict[str, Any]]) -> int:
    seq_values = [event.get("seq") for event in events if isinstance(event.get("seq"), int)]
    return max(seq_values, default=0) + 1


def _seen_interrupt_ids(events: list[dict[str, Any]]) -> set[str]:
    seen: set[str] = set()
    for event in events:
        for interrupt in protocol_interrupts_from_event(event):
            seen.add(interrupt["id"])
    return seen


def _namespace_from_interrupt(interrupt: Any) -> list[str]:
    # ``langgraph.types.Interrupt`` defines ``__slots__ == ("value", "id")`` (verified
    # against LangGraph 1.2.5), so it carries NO ``ns``/``namespace`` attribute and this
    # returns ``[]`` for every real interrupt — whether read from ``state.tasks`` or
    # recovered from checkpointer pending_writes. We keep this defensive lookup only in
    # case a future LangGraph release exposes a namespace attribute on the object itself.
    for attr in ("ns", "namespace"):
        value = getattr(interrupt, attr, None)
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            namespace = [segment for segment in value if isinstance(segment, str)]
            if namespace:
                return namespace
    return []


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
    task_payloads: list[PendingInputPayload] = []
    task_interrupt_ids: set[str] = set()
    for task in getattr(state, "tasks", ()) or ():
        # Real ``PregelTask.path`` is e.g. ``('__pregel_pull', 'tools')`` — there is no
        # ``node:task_id`` segment to recover a subgraph namespace from (that lives in
        # ``metadata['langgraph_checkpoint_ns']``, not ``path``). The namespace here is
        # therefore always empty; the resume round-trip validates the empty value.
        namespace: list[str] = []
        for index, interrupt in enumerate(getattr(task, "interrupts", ()) or ()):
            payload = _payload_from_interrupt(interrupt, namespace=namespace, index=index)
            if payload is not None:
                task_payloads.append(payload)
                task_interrupt_ids.add(payload["interrupt_id"])

    top_level_payloads: list[PendingInputPayload] = []
    interrupts = list(getattr(state, "interrupts", ()) or ())
    for index, interrupt in enumerate(interrupts):
        payload = _payload_from_interrupt(
            interrupt,
            namespace=_namespace_from_interrupt(interrupt),
            index=index,
        )
        if payload is not None:
            top_level_payloads.append(payload)

    if task_payloads:
        return task_payloads + [
            payload
            for payload in top_level_payloads
            if payload["interrupt_id"] not in task_interrupt_ids
        ]
    return top_level_payloads


def _thread_id_from_config(config: dict[str, Any]) -> str | None:
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        return None
    thread_id = configurable.get("thread_id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


def _append_unique_payloads(
    target: list[PendingInputPayload],
    source: list[PendingInputPayload],
) -> None:
    seen = {payload["interrupt_id"] for payload in target}
    for payload in source:
        interrupt_id = payload["interrupt_id"]
        if not interrupt_id or interrupt_id in seen:
            continue
        target.append(payload)
        seen.add(interrupt_id)


def _interrupt_payloads_from_pending_writes(
    pending_writes: Any,
) -> list[PendingInputPayload]:
    # Checkpointer-recovered interrupts always carry an EMPTY namespace here, and that
    # is correct and intentional:
    #   * The ``Interrupt`` object has no ``ns``/``namespace`` attr (slots: value, id),
    #     so ``_namespace_from_interrupt`` returns ``[]``.
    #   * pending_writes only carry ``(task_id, channel, value)`` — there is no real
    #     ``node:task_id`` path segment to rebuild a subgraph namespace from (an earlier
    #     "recover namespace from ``PregelTask.path``" attempt was a verified no-op:
    #     real paths look like ``('__pregel_pull', 'tools')`` with no ``:`` segment).
    #   * The empty namespace is validated by the resume round-trip (root-level resume
    #     default), and the hydration caller in ``conversation_agent_protocol_interrupts``
    #     does NOT pass any task namespaces — so empty is the only honest value.
    if not isinstance(pending_writes, Sequence) or isinstance(pending_writes, str | bytes):
        return []

    payloads: list[PendingInputPayload] = []
    for write in pending_writes:
        if not isinstance(write, Sequence) or isinstance(write, str | bytes) or len(write) < 3:
            continue
        channel = write[1]
        if channel != "__interrupt__":
            continue
        raw_interrupts = write[2]
        if not isinstance(raw_interrupts, Sequence) or isinstance(raw_interrupts, str | bytes):
            continue
        for index, interrupt in enumerate(raw_interrupts):
            payload = _payload_from_interrupt(
                interrupt,
                namespace=_namespace_from_interrupt(interrupt),
                index=index,
            )
            if payload is not None:
                payloads.append(payload)
    return payloads


async def interrupt_payloads_from_checkpointer(
    config: dict[str, Any],
) -> list[PendingInputPayload]:
    thread_id = _thread_id_from_config(config)
    if thread_id is None:
        return []

    try:
        from app.agent_runtime.checkpointer import get_checkpointer

        checkpointer = get_checkpointer()
        checkpoint_tuple = await checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
    except RuntimeError:
        return []
    except Exception:
        logger.warning("checkpointer pending interrupt lookup failed", exc_info=True)
        return []

    if checkpoint_tuple is None:
        return []
    return _interrupt_payloads_from_pending_writes(
        getattr(checkpoint_tuple, "pending_writes", None),
    )


async def pending_input_requested_events(
    agent: Any,
    config: dict[str, Any],
    *,
    run_id: str,
    thread_id: str,
    emitted: list[dict[str, Any]],
) -> list[StoredProtocolEvent]:
    get_state = getattr(agent, "aget_state", None)
    if not callable(get_state):
        return []
    try:
        state = await get_state(config)
    except Exception as exc:
        raise PendingInputStateUnavailable("pending input state unavailable") from exc

    payloads = _interrupt_payloads_from_state(state)
    # On the normal path ``state.tasks[].interrupts`` is already populated, so the
    # checkpointer fallback (a second ``aget_tuple`` DB read against the shared
    # checkpointer pool) is only needed when state carries no interrupts at all
    # (e.g. checkpointer-recovered runs where interrupts live in pending_writes).
    if not payloads:
        _append_unique_payloads(
            payloads,
            await interrupt_payloads_from_checkpointer(config),
        )

    seen_interrupt_ids = _seen_interrupt_ids(emitted)
    raw_interrupts: list[dict[str, Any]] = []
    for payload in payloads:
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
