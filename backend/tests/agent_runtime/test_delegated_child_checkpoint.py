from __future__ import annotations

from typing import Any, NotRequired
from uuid import uuid4

import pytest
from deepagents.graph import DeepAgentState
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_config
from langgraph.constants import CONFIG_KEY_CHECKPOINTER
from langgraph.graph import END, START, MessagesState, StateGraph

from app.agent_runtime.checkpoint_seed import CheckpointSeed
from app.agent_runtime.delegated_child_checkpoint import (
    child_instruction_message_id,
    saved_child_step,
)
from app.agent_runtime.lease_checkpoint_namespace import LeaseNamespaceAuthority
from app.agent_runtime.lease_checkpoint_saver import LeaseCheckpointSaver
from app.authz.org_context import OrgContext
from app.tenancy.checkpoint_audit import run_configurable


class ReceiptMessagesState(MessagesState):
    moldy_child_model_receipts: NotRequired[dict[str, Any]]


class ReceiptDeltaState(DeepAgentState):
    moldy_child_model_receipts: NotRequired[dict[str, Any]]


@pytest.mark.parametrize("with_marker", [True, False], ids=["verified-model", "unmarked-model"])
@pytest.mark.parametrize("leased", [False, True], ids=["direct", "lease"])
@pytest.mark.parametrize("delta", [False, True], ids=["messages", "delta-messages"])
async def test_proof_uses_active_child_saver_and_answered_message(
    leased: bool, delta: bool, with_marker: bool
) -> None:
    context = OrgContext(uuid4(), uuid4(), uuid4())
    thread, run, child_id, note, removed, trailing, unseen = (uuid4() for _ in range(7))
    config = run_configurable(str(thread), run, context.tenant_id)
    pool = InMemorySaver()
    saver = (
        LeaseCheckpointSaver(
            pool,
            seed=CheckpointSeed(str(thread), {}),
            run_id=str(run),
            namespaces=LeaseNamespaceAuthority(["tools"]),
        )
        if leased
        else pool
    )

    ids = {
        note: "한국 자료만",
        removed: "개인 자료",
        trailing: "출처도 추가",
        unseen: "다른 모델 경로",
    }
    child = StateGraph(ReceiptDeltaState if delta else ReceiptMessagesState)
    marker = {
        "child": str(child_id),
        "run": str(run),
        "tool": "lookup-1",
        "outputs": {
            "actual-model-output": [str(note), str(removed), str(trailing)],
            "unrelated-model-output": [str(unseen)],
        },
    }
    child.add_node(
        "model",
        lambda state: {
            "messages": [
                RemoveMessage(id=child_instruction_message_id(removed)),
                AIMessage(content="검토했어요", id="actual-model-output"),
                HumanMessage(content=ids[trailing], id=child_instruction_message_id(trailing)),
                AIMessage(content="검토를 중지했습니다", id="synthetic-guardrail"),
            ],
            **({"moldy_child_model_receipts": marker} if with_marker else {}),
        },
    )
    child.add_edge(START, "model")
    child.add_edge("model", END)
    child_graph = child.compile()

    async def dispatch(state: MessagesState) -> dict[str, Any]:
        caller = get_config()
        namespace = caller.get("configurable", {})["checkpoint_ns"]
        assert namespace.startswith("tools:")
        await child_graph.ainvoke(
            {
                "messages": [
                    HumanMessage(content=ids[key], id=child_instruction_message_id(key))
                    for key in (note, removed, unseen)
                ]
            },
            {"metadata": {"lc_subagent_tool_call_id": "lookup-1"}},
        )
        proof = await saved_child_step(
            context, thread, run, "lookup-1", namespace, child_id=child_id, messages_by_id=ids
        )
        assert proof and proof[1] == ({note} if with_marker else set())
        wrong_child = await saved_child_step(
            context, thread, run, "lookup-1", namespace, child_id=uuid4(), messages_by_id=ids
        )
        assert wrong_child == (proof[0], set())
        mismatched = await saved_child_step(
            context,
            thread,
            run,
            "lookup-1",
            namespace,
            child_id=child_id,
            messages_by_id={note: "다른 내용"},
        )
        assert mismatched == (proof[0], set())
        assert not await saved_child_step(
            context, thread, run, "lookup-2", namespace, child_id=child_id, messages_by_id=ids
        )
        assert not await saved_child_step(
            context, thread, uuid4(), "lookup-1", namespace, child_id=child_id, messages_by_id=ids
        )
        assert not await saved_child_step(
            context, uuid4(), run, "lookup-1", namespace, child_id=child_id, messages_by_id=ids
        )
        assert not await saved_child_step(
            OrgContext(context.org_id, uuid4(), context.user_id),
            thread,
            run,
            "lookup-1",
            namespace,
            child_id=child_id,
            messages_by_id=ids,
        )
        assert not await saved_child_step(
            context, thread, run, "lookup-1", "", child_id=child_id, messages_by_id=ids
        )
        assert not await saved_child_step(
            context, thread, run, "lookup-1", "tools:sibling", child_id=child_id, messages_by_id=ids
        )
        assert not await saved_child_step(
            context,
            thread,
            run,
            "lookup-1",
            namespace,
            child_id=child_id,
            messages_by_id=ids,
            config={
                "configurable": {**caller.get("configurable", {}), CONFIG_KEY_CHECKPOINTER: None}
            },
        )
        return {"messages": [AIMessage(content="done")]}

    parent = StateGraph(MessagesState)
    parent.add_node("tools", dispatch)
    parent.add_edge(START, "tools")
    parent.add_edge("tools", END)
    await parent.compile(checkpointer=saver).ainvoke(
        {"messages": [HumanMessage(content="discuss")]}, {"configurable": config}
    )
