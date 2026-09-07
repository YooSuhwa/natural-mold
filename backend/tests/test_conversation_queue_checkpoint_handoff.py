from __future__ import annotations

import asyncio
import contextlib
from typing import Literal

import anyio
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph


@pytest.mark.asyncio
async def test_committed_tool_result_survives_cancellation_handoff_without_replay() -> None:
    # Given a real checkpointed graph whose tool node completed before model work blocked.
    tool_calls = 0
    blocked = anyio.Event()

    async def tool_effect(state: MessagesState) -> dict[str, list[ToolMessage]]:
        del state
        nonlocal tool_calls
        tool_calls += 1
        return {
            "messages": [
                ToolMessage(
                    content="durable tool result",
                    tool_call_id="tool-call-1",
                    name="durable_tool",
                )
            ]
        }

    async def blocked_model(state: MessagesState) -> dict[str, list[AIMessage]]:
        del state
        blocked.set()
        await anyio.sleep_forever()
        return {"messages": []}

    async def corrected_model(state: MessagesState) -> dict[str, list[AIMessage]]:
        del state
        return {"messages": [AIMessage(content="corrected answer")]}

    def route_from_committed_state(state: MessagesState) -> Literal["tool", "corrected"]:
        if any(isinstance(message, ToolMessage) for message in state["messages"]):
            return "corrected"
        return "tool"

    builder = StateGraph(MessagesState)
    builder.add_node("tool", tool_effect)
    builder.add_node("blocked", blocked_model)
    builder.add_node("corrected", corrected_model)
    builder.add_conditional_edges(START, route_from_committed_state)
    builder.add_edge("tool", "blocked")
    builder.add_edge("blocked", END)
    builder.add_edge("corrected", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config: RunnableConfig = {"configurable": {"thread_id": "queue-checkpoint-handoff"}}

    first = asyncio.create_task(
        graph.ainvoke({"messages": [HumanMessage(content="initial request")]}, config)
    )
    with anyio.fail_after(2):
        await blocked.wait()
    committed = await graph.aget_state(config)
    assert tool_calls == 1
    assert any(
        isinstance(message, ToolMessage) and message.content == "durable tool result"
        for message in committed.values["messages"]
    )

    # When cancellation ends the blocked execution and a correction uses the same thread.
    first.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="corrected direction")]},
        config,
    )

    # Then checkpoint state is handed off and the completed tool effect is not replayed.
    assert tool_calls == 1
    assert any(
        isinstance(message, ToolMessage) and message.content == "durable tool result"
        for message in result["messages"]
    )
    assert result["messages"][-1].content == "corrected answer"
