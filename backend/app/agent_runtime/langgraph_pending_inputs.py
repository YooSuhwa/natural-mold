from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, TypedDict, cast

from langgraph.pregel._task_status import read_task_statuses

from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    canonical_input_requested_events,
    protocol_interrupts_from_event,
    stored_protocol_event,
)
from app.agent_runtime.streaming import _interrupt_to_standard_chunk
from app.tenancy.checkpoint_audit import CHECKPOINT_TENANT_KEY

logger = logging.getLogger(__name__)


class PendingInputPayload(TypedDict):
    interrupt_id: str
    payload: dict[str, Any]
    namespace: list[str]


class PendingInputStateUnavailable(RuntimeError):
    pass


def _next_protocol_seq(events: list[StoredProtocolEvent]) -> int:
    return max((event["seq"] for event in events), default=0) + 1


def _seen_interrupt_ids(events: list[StoredProtocolEvent]) -> set[str]:
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
    # Monotonic across ALL tasks (and the top-level scan below) so two id-less
    # interrupts in different tasks don't both synthesize ``interrupt-1`` and
    # collide under id-based dedup (ADR-021 review #8).
    fallback_index = 0
    for task in getattr(state, "tasks", ()) or ():
        # Real ``PregelTask.path`` is e.g. ``('__pregel_pull', 'tools')`` — there is no
        # ``node:task_id`` segment to recover a subgraph namespace from (that lives in
        # ``metadata['langgraph_checkpoint_ns']``, not ``path``). The namespace here is
        # therefore always empty; the resume round-trip validates the empty value.
        namespace: list[str] = []
        for interrupt in getattr(task, "interrupts", ()) or ():
            payload = _payload_from_interrupt(interrupt, namespace=namespace, index=fallback_index)
            fallback_index += 1
            if payload is not None:
                task_payloads.append(payload)
                task_interrupt_ids.add(payload["interrupt_id"])

    top_level_payloads: list[PendingInputPayload] = []
    interrupts = list(getattr(state, "interrupts", ()) or ())
    for interrupt in interrupts:
        payload = _payload_from_interrupt(
            interrupt,
            namespace=_namespace_from_interrupt(interrupt),
            index=fallback_index,
        )
        fallback_index += 1
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
    # Monotonic across the WHOLE scan, not per-write: a per-write ``enumerate``
    # would synthesize ``interrupt-1`` for the first id-less interrupt of every
    # write, and two such writes would collide → dedup drops one interrupt on
    # checkpointer recovery (ADR-021 review #8). Real ``Interrupt`` objects carry
    # a content-derived id so the fallback is rare, but a unique fallback keeps
    # it correct regardless.
    fallback_index = 0
    for write in pending_writes:
        if not isinstance(write, Sequence) or isinstance(write, str | bytes) or len(write) < 3:
            continue
        channel = write[1]
        if channel != "__interrupt__":
            continue
        raw_interrupts = write[2]
        if not isinstance(raw_interrupts, Sequence) or isinstance(raw_interrupts, str | bytes):
            continue
        for interrupt in raw_interrupts:
            payload = _payload_from_interrupt(
                interrupt,
                namespace=_namespace_from_interrupt(interrupt),
                index=fallback_index,
            )
            fallback_index += 1
            if payload is not None:
                payloads.append(payload)
    return payloads


async def interrupt_payloads_from_checkpointer(
    config: dict[str, Any],
    saver: Callable[[], Any] | None = None,
) -> list[PendingInputPayload]:
    """``saver`` is a gated factory (request reads); the run stream reads its own thread."""
    thread_id = _thread_id_from_config(config)
    if thread_id is None:
        return []

    try:
        from app.agent_runtime.checkpointer import get_checkpointer

        checkpointer = saver() if saver is not None else get_checkpointer()
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


def _snapshot_checkpoint(state: Any, scope: Mapping[str, Any]) -> str | None:
    config = getattr(state, "config", None)
    details = config.get("configurable") if isinstance(config, Mapping) else None
    if (
        not isinstance(details, Mapping)
        or details.get("thread_id") != scope.get("thread_id")
        or details.get("checkpoint_ns", "") != scope.get("checkpoint_ns", "")
    ):
        return None
    checkpoint = details.get("checkpoint_id")
    return checkpoint if isinstance(checkpoint, str) and checkpoint else None


async def _pinned_run_output(
    agent: Any, config: dict[str, Any], scope: Mapping[str, Any], run_id: str
) -> tuple[Any, bool]:
    # The graph executes at its pinned input. Only the terminal read is live:
    # explicit checkpoint_id in aget_state returns the historical, replayable input.
    coordinates = dict(scope)
    seed = coordinates.pop("checkpoint_id")
    live = {**config, "configurable": coordinates}
    tenant = coordinates.get(CHECKPOINT_TENANT_KEY)
    if coordinates.get("moldy_run_id") != run_id or not isinstance(tenant, str) or not tenant:
        raise PendingInputStateUnavailable("pending input state unavailable")
    expected = {"moldy_run_id": run_id, CHECKPOINT_TENANT_KEY: tenant}
    output = await agent.aget_state(live)

    def owned(state: Any) -> bool:
        metadata = getattr(state, "metadata", None) or {}
        return bool(_snapshot_checkpoint(state, scope)) and all(
            metadata.get(key) == value for key, value in expected.items()
        )

    if owned(output):
        return output, True
    if _snapshot_checkpoint(output, scope) == seed:
        # A partially resumed parallel step can still occupy its (older) input
        # checkpoint. The live snapshot applies task writes to that exact seed.
        metadata = getattr(output, "metadata", None) or {}
        if metadata.get(CHECKPOINT_TENANT_KEY) in (None, tenant):
            return output, True
    # Another authorized branch may have advanced this thread. Only inspect
    # an output stamped by our physical run and company in the same namespace.
    async for state in agent.aget_state_history(live, filter=expected, limit=1):
        if owned(state):
            return state, False
    raise PendingInputStateUnavailable("pending input state unavailable")


async def _pinned_pending_writes(agent: Any, state: Any) -> list[PendingInputPayload]:
    if getattr(state, "next", None) == () and not getattr(state, "tasks", ()):
        return []
    saver = getattr(agent, "checkpointer", None)
    if saver is None or not callable(getattr(saver, "aget_tuple", None)):
        from app.agent_runtime.checkpointer import get_checkpointer

        saver = get_checkpointer()
    saved = await saver.aget_tuple(state.config)
    coordinates = state.config["configurable"]
    if (
        saved is None
        or _snapshot_checkpoint(saved, coordinates) != coordinates["checkpoint_id"]
        or saved.checkpoint["id"] != coordinates["checkpoint_id"]
        or any(
            saved.metadata.get(key) != (state.metadata or {}).get(key)
            for key in ("moldy_run_id", CHECKPOINT_TENANT_KEY)
        )
    ):
        raise PendingInputStateUnavailable("pending input state unavailable")
    writes = [
        (task_id, "__interrupt__", task.pending_interrupts)
        for task_id, task in read_task_statuses(saved.pending_writes or ()).items()
    ]
    return _interrupt_payloads_from_pending_writes(writes)


async def pending_input_requested_events(
    agent: Any,
    config: dict[str, Any],
    *,
    run_id: str,
    thread_id: str,
    emitted: list[StoredProtocolEvent],
) -> list[StoredProtocolEvent]:
    raw_get_state = getattr(agent, "aget_state", None)
    if not callable(raw_get_state):
        return []
    get_state = cast(Callable[[dict[str, Any]], Awaitable[Any]], raw_get_state)
    coordinates = config.get("configurable") or {}
    pinned = bool(coordinates.get("checkpoint_id"))
    try:
        if pinned:
            state, live = await _pinned_run_output(agent, config, coordinates, run_id)
        else:
            state, live = await get_state(config), True
    except Exception as exc:
        raise PendingInputStateUnavailable("pending input state unavailable") from exc

    # Historical snapshots include already answered interrupts. Pending writes on
    # the verified output carry their current task status, even after a branch move.
    payloads = _interrupt_payloads_from_state(state) if live else []
    # On the normal path ``state.tasks[].interrupts`` is already populated, so the
    # checkpointer fallback (a second ``aget_tuple`` DB read against the shared
    # checkpointer pool) is only needed when state carries no interrupts at all
    # (e.g. checkpointer-recovered runs where interrupts live in pending_writes).
    if not payloads:
        _append_unique_payloads(
            payloads,
            await _pinned_pending_writes(agent, state)
            if pinned
            else await interrupt_payloads_from_checkpointer(config),
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
