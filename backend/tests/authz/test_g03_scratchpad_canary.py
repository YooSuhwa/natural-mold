"""Canary for the private LangGraph scratchpad G-03 reads (``connection_guard_resume``).

If a LangGraph upgrade changes the scratchpad, these fail first; the guard itself then
falls back to "first answer only" instead of breaking runs.
"""

from __future__ import annotations

import importlib.metadata
from typing import Any, TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agent_runtime.runtime import connection_guard_resume
from app.agent_runtime.runtime.connection_guard_resume import recorded_answers


class _State(TypedDict):
    seen: list[int]


def test_pinned_langgraph_exposes_the_scratchpad_key() -> None:
    assert importlib.metadata.version("langgraph").startswith("1.2.")
    assert connection_guard_resume.CONFIG_KEY_SCRATCHPAD is not None


async def test_scratchpad_counts_the_answers_a_resumed_node_carries() -> None:
    counts: list[int] = []

    def node(state: _State) -> dict[str, Any]:
        counts.append(recorded_answers())
        interrupt("first")
        interrupt("second")
        return {"seen": counts}

    graph = StateGraph(_State)
    graph.add_node("n", node)
    graph.add_edge(START, "n")
    graph.add_edge("n", END)
    app = graph.compile(checkpointer=MemorySaver())
    config: Any = {"configurable": {"thread_id": "canary"}}

    await app.ainvoke({"seen": []}, config)
    await app.ainvoke(Command(resume="a"), config)
    await app.ainvoke(Command(resume="b"), config)
    assert counts == [0, 1, 2]


def test_an_unreadable_scratchpad_falls_back_to_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    key = connection_guard_resume.CONFIG_KEY_SCRATCHPAD
    monkeypatch.setattr(
        connection_guard_resume, "get_config", lambda: {"configurable": {key: object()}}
    )
    assert recorded_answers() == 0
