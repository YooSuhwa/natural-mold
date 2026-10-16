"""Resume through LeaseCheckpointSaver keeps seed writes, seed immutability and child pauses."""

from __future__ import annotations

from typing import Any, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agent_runtime.checkpoint_seed import capture_checkpoint_seed
from app.agent_runtime.lease_checkpoint_namespace import LeaseNamespaceAuthority
from app.agent_runtime.lease_checkpoint_saver import LeaseCheckpointSaver

THREAD = "resume-thread"


class State(TypedDict, total=False):
    log: list[str]
    first: str
    second: str


async def _lease(upstream: InMemorySaver, run_id: str) -> LeaseCheckpointSaver:
    seed = await capture_checkpoint_seed(
        upstream,
        thread_id=THREAD,
        checkpoint_id=None,
        delta_channels=(),
        child_node_names=("child",),
    )
    return LeaseCheckpointSaver(
        upstream, seed=seed, run_id=run_id, namespaces=LeaseNamespaceAuthority({"child"})
    )


def _config(run_id: str) -> Any:
    return {"configurable": {"thread_id": THREAD, "moldy_run_id": run_id}}


def _two_interrupts(state: State) -> dict[str, Any]:
    first = interrupt("first?")
    second = interrupt("second?")
    return {"first": first, "second": second}


def _double_gate_graph(saver: Any) -> Any:
    graph = StateGraph(State)
    graph.add_node("gate", _two_interrupts)
    graph.add_edge(START, "gate")
    graph.add_edge("gate", END)
    return graph.compile(checkpointer=saver)


@pytest.mark.asyncio
async def test_seed_reads_include_this_leases_seed_writes_and_seed_stays_frozen() -> None:
    upstream = InMemorySaver()
    await _double_gate_graph(await _lease(upstream, "run-1")).ainvoke({}, _config("run-1"))

    lease = await _lease(upstream, "run-2")
    frozen = lease._seed.entries[""].saved
    frozen_versions = dict(frozen.checkpoint["channel_versions"])
    graph = _double_gate_graph(lease)
    await graph.ainvoke(Command(resume="one"), _config("run-2"))

    via_lease = await graph.aget_state(_config("run-2"))
    plain = await _double_gate_graph(upstream).aget_state(_config("run-2"))
    assert [item.value for item in via_lease.interrupts] == ["second?"]
    assert [item.value for item in via_lease.interrupts] == [i.value for i in plain.interrupts]
    assert frozen.checkpoint["channel_versions"] == frozen_versions


def _child_graph(calls: list[str]) -> Any:
    def pre(state: State) -> dict[str, Any]:
        calls.append("pre")
        return {"log": [*state.get("log", []), "pre"]}

    def ask(state: State) -> dict[str, Any]:
        return {"first": interrupt("child?")}

    child = StateGraph(State)
    child.add_node("pre", pre)
    child.add_node("ask", ask)
    child.add_edge(START, "pre")
    child.add_edge("pre", "ask")
    child.add_edge("ask", END)
    return child.compile()


def _parent_graph(saver: Any, calls: list[str]) -> Any:
    graph = StateGraph(State)
    graph.add_node("child", _child_graph(calls))
    graph.add_edge(START, "child")
    graph.add_edge("child", END)
    return graph.compile(checkpointer=saver)


@pytest.mark.asyncio
async def test_resume_continues_paused_child_without_replaying_it() -> None:
    upstream = InMemorySaver()
    calls: list[str] = []
    await _parent_graph(await _lease(upstream, "run-a"), calls).ainvoke({}, _config("run-a"))
    assert calls == ["pre"]

    lease = await _lease(upstream, "run-b")
    assert any(ns.startswith("child:") for ns in lease._seed.entries)
    result = await _parent_graph(lease, calls).ainvoke(Command(resume="ok"), _config("run-b"))

    assert calls == ["pre"]
    assert result["first"] == "ok"
    assert result["log"] == ["pre"]


@pytest.mark.asyncio
async def test_allowlist_clone_serializes_through_derived_upstream_serde() -> None:
    upstream = InMemorySaver()
    lease = await _lease(upstream, "run-s")

    clone = lease.with_allowlist([("tests.authz", "Marker")])

    if clone is not lease:
        assert clone.serde is clone._upstream.serde
        assert clone._upstream is not upstream
    assert clone._lineage is lease._lineage
