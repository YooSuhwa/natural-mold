"""Installed LangGraph runs through LeaseCheckpointSaver.

Covers DeltaChannel message history across leases (seed without a stored
snapshot), catalog-authorized nested subgraph namespaces, interrupt/resume
pending writes, fork from an explicit checkpoint, and cross-lease rejection.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, TypedDict

import pytest
from langgraph.channels.delta import DeltaChannel
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agent_runtime.checkpoint_seed import capture_checkpoint_seed
from app.agent_runtime.lease_checkpoint_namespace import (
    CheckpointLineageDeniedError,
    LeaseNamespaceAuthority,
)
from app.agent_runtime.lease_checkpoint_saver import LeaseCheckpointSaver

THREAD = "installed-thread"


def _append(state: Any, writes: Sequence[Any]) -> Any:
    merged = list(state or [])
    for item in (item for batch in writes for item in batch):
        if item not in merged:
            merged.append(item)
    return merged


class State(TypedDict, total=False):
    messages: Annotated[list[str], DeltaChannel(_append, snapshot_frequency=50)]
    gate: str


class ReadTrackingSaver(InMemorySaver):
    def __init__(self) -> None:
        super().__init__()
        self.read_ids: list[str | None] = []

    async def aget_tuple(self, config: Any) -> Any:
        self.read_ids.append(config["configurable"].get("checkpoint_id"))
        return await super().aget_tuple(config)


def _echo(state: State) -> dict[str, Any]:
    return {"messages": [f"child-saw-{len(state.get('messages', []))}"]}


def _speak(state: State) -> dict[str, Any]:
    return {"messages": [f"parent-{len(state.get('messages', []))}"]}


def _child_graph() -> Any:
    child = StateGraph(State)
    child.add_node("echo", _echo)
    child.add_edge(START, "echo")
    child.add_edge("echo", END)
    return child.compile()


def _graph(saver: LeaseCheckpointSaver, *, with_gate: bool = False) -> Any:
    child = _child_graph()
    graph = StateGraph(State)
    graph.add_node("speak", _speak)
    graph.add_node("child", child)
    graph.add_edge(START, "speak")
    graph.add_edge("speak", "child")
    if with_gate:
        graph.add_node("gate", lambda state: {"gate": interrupt("approve?")})
        graph.add_edge("child", "gate")
        graph.add_edge("gate", END)
    else:
        graph.add_edge("child", END)
    return graph.compile(checkpointer=saver)


async def _lease(
    upstream: InMemorySaver, run_id: str, checkpoint_id: str | None = None
) -> LeaseCheckpointSaver:
    seed = await capture_checkpoint_seed(
        upstream,
        thread_id=THREAD,
        checkpoint_id=checkpoint_id,
        delta_channels=("messages",),
        child_node_names=("child",),
    )
    return LeaseCheckpointSaver(
        upstream, seed=seed, run_id=run_id, namespaces=LeaseNamespaceAuthority({"child"})
    )


def _config(run_id: str, checkpoint_id: str | None = None) -> Any:
    configurable: dict[str, Any] = {"thread_id": THREAD, "moldy_run_id": run_id}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


@pytest.mark.asyncio
async def test_delta_history_nested_namespaces_and_second_lease_seed() -> None:
    upstream = ReadTrackingSaver()
    first = await _lease(upstream, "run-1")
    result = await _graph(first).ainvoke({"messages": ["hi"]}, _config("run-1"))
    assert result["messages"] == ["hi", "parent-1", "child-saw-2"]
    assert any(ns.startswith("child:") for ns in first._lineage.owned)

    upstream.read_ids.clear()
    second = await _lease(upstream, "run-2")
    seed_reads = list(upstream.read_ids)
    second_result = await _graph(second).ainvoke({"messages": ["again"]}, _config("run-2"))

    assert seed_reads[0] is None
    root_seed = second._seed.entries[""].saved
    assert "messages" not in root_seed.checkpoint["channel_values"]
    assert second_result["messages"] == [
        *("hi", "parent-1", "child-saw-2", "again", "parent-4", "child-saw-5"),
    ]
    owned = {item for ids in second._lineage.owned.values() for item in ids}
    assert all(read in owned for read in upstream.read_ids[len(seed_reads) :])


@pytest.mark.asyncio
async def test_interrupt_resume_and_fork_stay_in_lineage() -> None:
    upstream = ReadTrackingSaver()
    first = await _lease(upstream, "run-a")
    await _graph(first, with_gate=True).ainvoke({"messages": ["q"]}, _config("run-a"))
    paused = await upstream.aget_tuple({"configurable": {"thread_id": THREAD, "checkpoint_ns": ""}})
    assert paused is not None and paused.pending_writes

    resume = await _lease(upstream, "run-b")
    resumed = await _graph(resume, with_gate=True).ainvoke(Command(resume="yes"), _config("run-b"))
    assert resumed["gate"] == "yes"

    history = [item async for item in upstream.alist({"configurable": {"thread_id": THREAD}})]
    fork_point = next(
        item
        for item in history
        if item.metadata.get("source") == "input" and not item.metadata.get("parents")
    )
    fork_id = fork_point.config.get("configurable", {})["checkpoint_id"]
    fork = await _lease(upstream, "run-c", fork_id)
    forked = await _graph(fork).ainvoke({"messages": ["edited"]}, _config("run-c", fork_id))
    assert forked["messages"] == ["edited", "parent-1", "child-saw-2"]


@pytest.mark.asyncio
async def test_cross_lease_namespace_and_run_injection_rejected() -> None:
    upstream = ReadTrackingSaver()
    lease = await _lease(upstream, "run-x")

    with pytest.raises(CheckpointLineageDeniedError):
        await _graph(lease).ainvoke({"messages": ["x"]}, _config("run-other"))
    restricted = LeaseCheckpointSaver(
        upstream,
        seed=lease._seed,
        run_id="run-y",
        namespaces=LeaseNamespaceAuthority(()),
    )
    with pytest.raises(CheckpointLineageDeniedError):
        await _graph(restricted).ainvoke({"messages": ["x"]}, _config("run-y"))
