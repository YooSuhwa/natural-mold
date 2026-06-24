from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langgraph.types import Interrupt

from app.agent_runtime.langgraph_pending_inputs import (
    _interrupt_payloads_from_pending_writes,
    pending_input_requested_events,
)

_APPROVAL_VALUE = {
    "action_requests": [{"name": "execute_in_skill", "args": {"command": "make-docx"}}],
    "review_configs": [
        {"action_name": "execute_in_skill", "allowed_decisions": ["approve", "reject"]}
    ],
}


class _StateBackedAgent:
    def __init__(self, state: SimpleNamespace) -> None:
        self._state = state

    async def aget_state(self, _config: dict[str, Any]) -> SimpleNamespace:
        return self._state


def _approval_interrupt(interrupt_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=interrupt_id,
        value=dict(_APPROVAL_VALUE),
    )


def _real_approval_interrupt(interrupt_id: str) -> Interrupt:
    # A genuine LangGraph Interrupt (slots: value, id) — no ns/namespace attribute,
    # exactly what state.tasks and checkpointer recovery yield.
    return Interrupt(value=dict(_APPROVAL_VALUE), id=interrupt_id)


class _CheckpointerBackedAgent:
    async def aget_state(self, _config: dict[str, Any]) -> SimpleNamespace:
        return SimpleNamespace(interrupts=[], tasks=[])


class _PendingWritesCheckpointer:
    def __init__(self, pending_writes: list[tuple[str, str, list[Any]]]) -> None:
        self.pending_writes = pending_writes

    async def aget_tuple(self, _config: dict[str, Any]) -> SimpleNamespace:
        return SimpleNamespace(pending_writes=self.pending_writes)


@pytest.mark.asyncio
async def test_pending_input_events_empty_namespace_for_real_task_path() -> None:
    # Real ``PregelTask.path`` is ``('__pregel_pull', 'tools')`` (verified against
    # LangGraph 1.2.5) — there is NO ``node:task_id`` segment to recover a subgraph
    # namespace from, so the emitted namespace is empty. This locks in the actual
    # behavior so nobody re-adds a false "namespace is recovered from path" claim.
    state = SimpleNamespace(
        interrupts=[],
        tasks=[
            SimpleNamespace(
                id="task-1",
                path=("__pregel_pull", "tools"),
                interrupts=[_real_approval_interrupt("intr-real")],
            )
        ],
    )

    events = await pending_input_requested_events(
        _StateBackedAgent(state),
        {"configurable": {"thread_id": "thread-1"}},
        run_id="run-1",
        thread_id="thread-1",
        emitted=[],
    )

    assert len(events) == 1
    assert events[0]["method"] == "input.requested"
    assert events[0]["data"]["interrupt_id"] == "intr-real"
    assert events[0]["namespace"] == []
    assert events[0]["data"]["namespace"] == []


@pytest.mark.asyncio
async def test_pending_input_events_reads_checkpointer_pending_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.agent_runtime.checkpointer.get_checkpointer",
        lambda: _PendingWritesCheckpointer(
            [("task-1", "__interrupt__", [_real_approval_interrupt("intr-checkpoint")])]
        ),
    )

    events = await pending_input_requested_events(
        _CheckpointerBackedAgent(),
        {"configurable": {"thread_id": "thread-1"}},
        run_id="run-1",
        thread_id="thread-1",
        emitted=[],
    )

    assert len(events) == 1
    assert events[0]["method"] == "input.requested"
    assert events[0]["data"]["interrupt_id"] == "intr-checkpoint"
    assert events[0]["data"]["payload"]["action_requests"][0]["name"] == "execute_in_skill"
    # Checkpointer-recovered interrupts carry an empty namespace — validated by the
    # resume round-trip and intentional (see _interrupt_payloads_from_pending_writes).
    assert events[0]["namespace"] == []
    assert events[0]["data"]["namespace"] == []


def test_pending_writes_real_interrupt_has_empty_namespace() -> None:
    # A real langgraph Interrupt exposes NO ns/namespace attr, and pending_writes carry
    # only (task_id, channel, value) — there is no path segment to recover a namespace
    # from. The recovered namespace must therefore be ``[]`` (root-level resume default).
    payloads = _interrupt_payloads_from_pending_writes(
        [("task-1", "__interrupt__", [_real_approval_interrupt("intr-real")])]
    )

    assert len(payloads) == 1
    assert payloads[0]["interrupt_id"] == "intr-real"
    assert payloads[0]["namespace"] == []


@pytest.mark.asyncio
async def test_pending_input_events_skips_checkpointer_when_state_has_interrupts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The normal path already has interrupts in state.tasks; the checkpointer fallback
    # (a second aget_tuple DB read) must NOT be queried.
    state = SimpleNamespace(
        interrupts=[],
        tasks=[
            SimpleNamespace(
                id="task-1",
                path=("__pregel_pull", "tools"),
                interrupts=[_approval_interrupt("intr-state")],
            )
        ],
    )

    def _fail_get_checkpointer() -> Any:
        raise AssertionError("checkpointer fallback must not be queried")

    monkeypatch.setattr(
        "app.agent_runtime.checkpointer.get_checkpointer",
        _fail_get_checkpointer,
    )

    events = await pending_input_requested_events(
        _StateBackedAgent(state),
        {"configurable": {"thread_id": "thread-1"}},
        run_id="run-1",
        thread_id="thread-1",
        emitted=[],
    )

    assert len(events) == 1
    assert events[0]["data"]["interrupt_id"] == "intr-state"
