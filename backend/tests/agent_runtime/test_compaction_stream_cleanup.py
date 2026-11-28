from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from app.agent_runtime.event_broker import EventBroker
from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.config import settings
from tests.agent_runtime.langgraph_streaming_fixtures import sse_payload


@pytest.mark.asyncio
@pytest.mark.parametrize("marker_enabled", [True, False], ids=["markers", "no-markers"])
@pytest.mark.parametrize("fallback", [False, True], ids=["v3", "stream-mode"])
async def test_closing_worker_stream_cancels_in_flight_graph_node(
    monkeypatch: pytest.MonkeyPatch, marker_enabled: bool, fallback: bool
) -> None:
    monkeypatch.setattr(settings, "compaction_marker_enabled", marker_enabled)
    entered = asyncio.Event()
    release = asyncio.Event()
    cancelled = asyncio.Event()
    model = GenericFakeChatModel(messages=iter([AIMessage(content="working")]))

    async def wait_after_model(state: MessagesState) -> dict[str, list[AIMessage]]:
        await model.ainvoke(state["messages"])
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return {"messages": [AIMessage(content="finished")]}

    builder = StateGraph(MessagesState)
    builder.add_node(wait_after_model)
    builder.add_edge(START, "wait_after_model")
    builder.add_edge("wait_after_model", END)
    graph = builder.compile()
    agent = SimpleNamespace(astream=graph.astream) if fallback else graph
    source = stream_agent_response_langgraph(
        agent,
        [HumanMessage(content="start")],
        {"configurable": {"thread_id": "cleanup"}},
        run_id="cleanup",
    )
    try:
        async with asyncio.timeout(10), aclosing(source):
            async for frame in source:
                if sse_payload(frame)["method"] == "messages":
                    await entered.wait()
                    break
        assert entered.is_set()
        assert cancelled.is_set(), "closing the worker stream left its graph node running"
    finally:
        release.set()


@pytest.mark.asyncio
async def test_cleanup_error_still_flushes_and_closes_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "compaction_marker_enabled", False)

    async def failing_stream(*_: Any, **__: Any) -> AsyncIterator[tuple[str, dict[str, int]]]:
        try:
            yield "values", {"progress": 1}
        finally:
            raise RuntimeError("upstream close failed")

    persisted: list[dict[str, Any]] = []
    broker = EventBroker("cleanup-error")

    async def persist(batch: list[dict[str, Any]]) -> None:
        persisted.extend(batch)

    source = stream_agent_response_langgraph(
        SimpleNamespace(astream=failing_stream),
        [],
        {},
        broker=broker,
        persist_callback=persist,
        run_id="cleanup-error",
    )
    async for frame in source:
        if sse_payload(frame)["method"] == "values":
            break
    with pytest.raises(RuntimeError, match="upstream close failed"):
        await source.aclose()

    async with asyncio.timeout(2):
        delivered = [event async for event in broker.subscribe()]
    assert [event["method"] for event in persisted] == ["lifecycle", "values"]
    assert [event["data"]["method"] for event in delivered] == ["lifecycle", "values"]
