"""Only confirmed, owner-scoped directives can supplement the original recovery turn."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent_runtime.chat_steer_middleware import RunSteerState
from app.agent_runtime.chat_steer_model_delivery import steer_message_id
from app.agent_runtime.model_input_receipt import text_digest
from app.agent_runtime.recovery_stream import RecoveryStreamCursor, continue_protocol_delivery
from app.agent_runtime.run_recovery_checkpoint import (
    RecoveryCheckpointBlocked,
    checked_head,
    turn_tools,
    verified_resume_config,
)
from app.schemas.conversation_graph_input import STEER_INPUT_MARKER
from app.services.conversation_run_fence import run_fence_scope
from tests.agent_runtime.test_recovery_checkpoint_graph import context, enrolled_input


def instruction(row_id: UUID, text: str) -> HumanMessage:
    return HumanMessage(
        content=text, id=steer_message_id(row_id), response_metadata={STEER_INPUT_MARKER: True}
    )


def tool_call(call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": call_id, "args": {}, "name": "read"}])


def completion(call_id: str) -> ToolMessage:
    return ToolMessage(name="read", tool_call_id=call_id, content="record")


async def test_recompiled_graph_preserves_tool_receipts_before_and_after_confirmed_steers() -> None:
    fence, cfg, config = context()
    first, second = uuid4(), uuid4()
    claimed = {str(first): text_digest("price only"), str(second): text_digest("domestic only")}
    effects: list[str] = []

    def first_read(state: RunSteerState) -> dict[str, Any]:
        effects.append("read-price")
        return {
            "messages": [
                tool_call("read-price"),
                completion("read-price"),
                instruction(first, "price only"),
            ],
            "moldy_run_steer_receipts": {
                "run_id": str(fence.run_id),
                "ids": [str(first)],
                "staged": {},
            },
        }

    def second_read(state: RunSteerState) -> dict[str, Any]:
        effects.append("read-country")
        return {
            "messages": [
                tool_call("read-country"),
                completion("read-country"),
                instruction(second, "domestic only"),
            ],
            "moldy_run_steer_receipts": {
                "run_id": str(fence.run_id),
                "ids": list(claimed),
                "staged": {},
            },
        }

    def finish(state: RunSteerState) -> dict[str, Any]:
        return {"messages": [AIMessage(content="complete")]}

    builder = StateGraph(RunSteerState)
    builder.add_node("price", first_read)
    builder.add_node("country", second_read)
    builder.add_node("finish", finish)
    builder.add_edge(START, "price")
    builder.add_edge("price", "country")
    builder.add_edge("country", "finish")
    builder.add_edge("finish", END)
    saver = InMemorySaver()
    graph = builder.compile(checkpointer=saver, interrupt_before=["finish"])
    await graph.ainvoke({"messages": [enrolled_input(fence)]}, config, durability="sync")
    checkpoint_id = checked_head(await saver.aget_tuple(config), cfg, fence.run_id)
    resumed = builder.compile(checkpointer=saver)
    cfg = replace(cfg, checkpoint_id=checkpoint_id)
    exact = {"configurable": {**config.get("configurable", {}), "checkpoint_id": checkpoint_id}}
    cursor = replace(
        RecoveryStreamCursor.from_events(str(fence.run_id), cfg.thread_id, []),
        claimed_steers=claimed,
        visible_tool_call_ids=frozenset(effects),
    )
    with run_fence_scope(fence), continue_protocol_delivery(cursor):
        verified = await verified_resume_config(resumed, exact, cfg, str(fence.run_id))
        result = await resumed.ainvoke(None, cast(RunnableConfig, verified))
    assert [
        message.content for message in result["messages"] if isinstance(message, HumanMessage)
    ] == ["look up the record", "price only", "domestic only"]
    assert effects == ["read-price", "read-country"]


@pytest.mark.parametrize(
    "defect",
    [
        "staged",
        "foreign_run",
        "missing_row",
        "changed_text",
        "wrong_marker",
        "malformed_marker",
        "unknown_metadata",
        "altered_role",
        "wrong_id",
        "duplicate_message",
        "missing_message",
        "unordered_receipts",
        "incomplete_tool",
        "marked_root",
        "late_unmarked_human",
        "unknown_receipt_field",
    ],
)
def test_unproven_supplement_never_anchors_a_continuation(defect: str) -> None:
    fence, *_ = context()
    first, second = uuid4(), uuid4()
    messages: list[BaseMessage] = [
        enrolled_input(fence),
        tool_call("previous"),
        completion("previous"),
        instruction(first, "one"),
        instruction(second, "two"),
    ]
    claims = {str(first): text_digest("one"), str(second): text_digest("two")}
    receipt: dict[str, Any] = {"run_id": str(fence.run_id), "staged": {}, "ids": list(claims)}
    match defect:
        case "staged":
            receipt["ids"].pop()
            receipt["staged"][str(second)] = claims[str(second)]
        case "foreign_run":
            receipt["run_id"] = str(uuid4())
        case "missing_row":
            del claims[str(first)]
        case "changed_text":
            messages[-1].content = "changed"
        case "wrong_marker":
            messages[-1].response_metadata.clear()
        case "malformed_marker":
            messages[-1].response_metadata[STEER_INPUT_MARKER] = 1
        case "unknown_metadata":
            messages[-1].response_metadata["moldy_recovery_input_run"] = str(fence.run_id)
        case "altered_role":
            messages[-1].name = "another sender"
        case "wrong_id":
            messages[-1].id = str(uuid4())
        case "duplicate_message":
            messages.append(messages[-1])
        case "missing_message":
            messages.pop()
        case "unordered_receipts":
            receipt["ids"].reverse()
        case "incomplete_tool":
            del messages[2]
        case "marked_root":
            messages[0].response_metadata[STEER_INPUT_MARKER] = True
        case "late_unmarked_human":
            messages.append(HumanMessage(content="new turn"))
        case "unknown_receipt_field":
            receipt["staged_ids"] = [str(first)]
    with pytest.raises(RecoveryCheckpointBlocked):
        turn_tools(messages, fence.run_id, steer_receipts=receipt, claimed_steers=claims)


def test_inherited_receipt_does_not_change_root_turn_boundary() -> None:
    fence, *_ = context()
    old, old_steer = uuid4(), uuid4()
    history = [HumanMessage(content="old"), instruction(old_steer, "previous instruction")]
    receipt = {"run_id": str(old), "staged": {}, "ids": [str(old_steer)]}
    tools = turn_tools(
        [*history, enrolled_input(fence), tool_call("current")],
        fence.run_id,
        steer_receipts=receipt,
    )
    assert tools.original_anchor_index == len(history)
    assert tools.requested == {"current"}
