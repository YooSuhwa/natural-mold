from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.base import CheckpointMetadata
from langgraph.types import Interrupt, StateSnapshot

from app.agent_runtime.langgraph_pending_inputs import (
    PendingInputStateUnavailable,
    _interrupt_payloads_from_pending_writes,
    pending_input_requested_events,
)
from app.tenancy.checkpoint_audit import CHECKPOINT_TENANT_KEY

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


def test_pending_writes_idless_interrupts_get_distinct_synthetic_ids() -> None:
    # ADR-021 re-review #8 — id-less interrupts across SEPARATE pending_writes
    # entries must get DISTINCT synthetic ids (monotonic across the whole scan).
    # A per-write enumerate gave both ``interrupt-1`` → id-based dedup downstream
    # dropped one interrupt on checkpointer recovery.
    idless_a = SimpleNamespace(value=dict(_APPROVAL_VALUE))  # no id attr → fallback fires
    idless_b = SimpleNamespace(value=dict(_APPROVAL_VALUE))
    payloads = _interrupt_payloads_from_pending_writes(
        [
            ("task-a", "__interrupt__", [idless_a]),
            ("task-b", "__interrupt__", [idless_b]),
        ]
    )

    ids = [p["interrupt_id"] for p in payloads]
    assert len(payloads) == 2
    assert len(set(ids)) == 2  # no collision
    assert ids == ["interrupt-1", "interrupt-2"]


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


def _snapshot(
    checkpoint: str,
    producer: str,
    *interrupts: str,
    tenant: str = "tenant-1",
    thread: str = "thread-1",
    namespace: str = "",
) -> StateSnapshot:
    return StateSnapshot(
        values={},
        next=("tools",),
        config={
            "configurable": {
                "thread_id": thread,
                "checkpoint_ns": namespace,
                "checkpoint_id": checkpoint,
            }
        },
        metadata=cast(
            CheckpointMetadata, {"moldy_run_id": producer, CHECKPOINT_TENANT_KEY: tenant}
        ),
        created_at=None,
        parent_config=None,
        tasks=(),
        interrupts=tuple(_real_approval_interrupt(item) for item in interrupts),
    )


class _ForkedAgent:
    def __init__(self, latest: StateSnapshot, *history: StateSnapshot) -> None:
        self.latest = latest
        self.history = history
        self.checkpointer = SimpleNamespace(aget_tuple=AsyncMock())

    async def aget_state(self, config: dict[str, Any]) -> StateSnapshot:
        if config["configurable"].get("checkpoint_id") == "seed":
            return _snapshot("seed", "parent", "already-answered", "still-pending")
        return self.latest

    async def aget_state_history(
        self,
        config: dict[str, Any],
        *,
        filter: dict[str, str],  # noqa: A002 - LangGraph signature
        limit: int,
    ) -> AsyncIterator[StateSnapshot]:
        assert config["configurable"]["thread_id"] == "thread-1"
        assert "checkpoint_id" not in config["configurable"]
        assert filter == {"moldy_run_id": "run-1", CHECKPOINT_TENANT_KEY: "tenant-1"}
        assert limit == 1
        for state in self.history:
            yield state


async def _pinned_events(agent: _ForkedAgent) -> list[str]:
    config = {
        "configurable": {
            "thread_id": "thread-1",
            "checkpoint_id": "seed",
            "moldy_run_id": "run-1",
            CHECKPOINT_TENANT_KEY: "tenant-1",
        }
    }
    events = await pending_input_requested_events(
        agent,
        config,
        run_id="run-1",
        thread_id="thread-1",
        emitted=[],
    )
    assert config["configurable"]["checkpoint_id"] == "seed"
    return [event["data"]["interrupt_id"] for event in events]


async def test_parallel_resume_reads_live_interrupts_at_the_accepted_seed() -> None:
    agent = _ForkedAgent(_snapshot("seed", "parent", "still-pending"))
    assert await _pinned_events(agent) == ["still-pending"]
    agent.checkpointer.aget_tuple.assert_not_called()


async def test_matching_seed_id_with_foreign_tenant_fails_closed() -> None:
    agent = _ForkedAgent(_snapshot("seed", "parent", "private", tenant="other-tenant"))
    with pytest.raises(PendingInputStateUnavailable):
        await _pinned_events(agent)
    agent.checkpointer.aget_tuple.assert_not_called()


@pytest.mark.parametrize("missing", ["moldy_run_id", CHECKPOINT_TENANT_KEY])
async def test_pinned_read_requires_all_current_run_coordinates(missing: str) -> None:
    coordinates = {
        "thread_id": "thread-1",
        "checkpoint_id": "seed",
        "moldy_run_id": "run-1",
        CHECKPOINT_TENANT_KEY: "tenant-1",
    }
    del coordinates[missing]
    agent = _ForkedAgent(_snapshot("output", "run-1", "still-pending"))
    with pytest.raises(PendingInputStateUnavailable):
        await pending_input_requested_events(
            agent, {"configurable": coordinates}, run_id="run-1", thread_id="thread-1", emitted=[]
        )
    agent.checkpointer.aget_tuple.assert_not_called()


async def test_own_history_uses_unanswered_writes_when_a_sibling_is_newer() -> None:
    history = _snapshot("our-output", "run-1", "already-answered", "still-pending")
    agent = _ForkedAgent(_snapshot("their-output", "sibling", "not-ours"), history)
    agent.checkpointer.aget_tuple.return_value = SimpleNamespace(
        config=history.config,
        metadata=history.metadata,
        checkpoint={"id": "our-output"},
        pending_writes=[
            ("finished", "__interrupt__", [_real_approval_interrupt("already-answered")]),
            ("finished", "__no_writes__", None),
            ("open", "__interrupt__", [_real_approval_interrupt("still-pending")]),
        ],
    )
    assert await _pinned_events(agent) == ["still-pending"]
    agent.checkpointer.aget_tuple.assert_awaited_once_with(history.config)


@pytest.mark.parametrize(
    "history",
    [
        None,
        _snapshot("other-thread-output", "run-1", "private", thread="other-thread"),
        _snapshot("other-company-output", "run-1", "private", tenant="other-tenant"),
        _snapshot("nested-output", "run-1", "private", namespace="child:task"),
        _snapshot("other-output", "other", "private"),
    ],
)
async def test_sibling_head_without_scoped_run_history_fails_closed(
    history: StateSnapshot | None,
) -> None:
    agent = _ForkedAgent(
        _snapshot("sibling", "sibling", "private"), *((history,) if history else ())
    )
    with pytest.raises(PendingInputStateUnavailable):
        await _pinned_events(agent)
    agent.checkpointer.aget_tuple.assert_not_called()


@pytest.mark.parametrize(
    "saved",
    [
        _snapshot("sibling", "run-1"),
        _snapshot("output", "other"),
        _snapshot("output", "run-1", tenant="other-tenant"),
    ],
)
async def test_fallback_does_not_expose_unrelated_raw_writes(saved: StateSnapshot) -> None:
    owned = _snapshot("output", "run-1")
    agent = _ForkedAgent(owned)
    agent.checkpointer.aget_tuple.return_value = SimpleNamespace(
        config=saved.config,
        metadata=saved.metadata,
        checkpoint={"id": saved.config.get("configurable", {}).get("checkpoint_id")},
        pending_writes=[("task", "__interrupt__", [_real_approval_interrupt("private")])],
    )
    with pytest.raises(PendingInputStateUnavailable):
        await _pinned_events(agent)
    agent.checkpointer.aget_tuple.assert_awaited_once_with(owned.config)
