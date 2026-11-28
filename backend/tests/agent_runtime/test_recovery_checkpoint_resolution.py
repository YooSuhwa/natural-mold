"""Only current-turn committed results and the owning prefix authorize a takeover."""

from __future__ import annotations

import uuid
from copy import deepcopy
from typing import cast
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import CheckpointMetadata, CheckpointTuple, empty_checkpoint

from app.agent_runtime.recovery_stream import RecoveryStreamCursor
from app.agent_runtime.run_recovery_checkpoint import RecoveryCheckpointBlocked
from app.models.conversation_run import ConversationRun
from app.services import conversation_run_recovery_checkpoint as recovery
from tests.agent_runtime.test_recovery_checkpoint_graph import context, enrolled_input


def setup_case(monkeypatch):
    fence, cfg, _ = context()
    cfg.agent_id = str(fence.run_id)
    run = ConversationRun(
        id=fence.run_id,
        conversation_id=fence.conversation_id,
        agent_id=fence.run_id,
        tenant_id=fence.tenant.tenant_id,
        org_id=fence.tenant.org_id,
        user_id=fence.tenant.user_id,
    )
    checkpoint = empty_checkpoint()
    first = AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": "current"}])
    second = AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": "pending"}])
    checkpoint["channel_values"]["messages"] = [
        HumanMessage(content="older turn"),
        AIMessage(content="", tool_calls=[{"name": "write", "args": {}, "id": "old"}]),
        ToolMessage(content="old result", tool_call_id="old", name="write"),
        enrolled_input(fence),
        first,
        ToolMessage(content="current result", tool_call_id="current", name="read"),
        second,
    ]
    saved = CheckpointTuple(
        {
            "configurable": {
                "thread_id": cfg.thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint["id"],
            }
        },
        checkpoint,
        cast(
            CheckpointMetadata,
            {"source": "loop", "moldy_run_id": str(run.id), "moldy_tenant_id": str(run.tenant_id)},
        ),
        None,
        [
            (
                "t",
                "messages",
                [ToolMessage(content="pending result", tool_call_id="pending", name="read")],
            )
        ],
    )
    saver = AsyncMock()
    saver.aget_tuple.return_value = saved
    gate = AsyncMock(return_value=saver)
    receipts = AsyncMock(return_value=None)
    cursor = RecoveryStreamCursor.from_events(str(run.id), cfg.thread_id, [])
    persisted = AsyncMock(return_value=cursor)
    monkeypatch.setattr(recovery, "conversation_checkpointer", gate)
    monkeypatch.setattr(recovery, "blocking_receipt_code", receipts)
    monkeypatch.setattr(recovery, "load_recovery_stream_cursor", persisted)
    return cfg, run, saver, gate, receipts, persisted


async def test_completed_effect_proof_requires_matching_call_in_committed_current_turn(monkeypatch):
    cfg, run, saver, gate, receipts, _ = setup_case(monkeypatch)
    db = AsyncMock()
    head = await recovery.resolve_recovery_checkpoint(db, run, cfg)
    assert head == saver.aget_tuple.return_value.checkpoint["id"]
    gate.assert_awaited_once_with(cfg.thread_id, cfg.org_context, db=db)
    receipts.assert_awaited_once_with(db, run.id, completed_call_ids={"current"})


async def test_wrong_tenant_stops_before_any_saver_read(monkeypatch):
    cfg, run, _, gate, _, _ = setup_case(monkeypatch)
    run.tenant_id = run.org_id
    with pytest.raises(RecoveryCheckpointBlocked):
        await recovery.resolve_recovery_checkpoint(AsyncMock(), run, cfg)
    gate.assert_not_awaited()


async def test_ambiguous_external_effect_takes_precedence_over_bad_stream(monkeypatch):
    cfg, run, _, _, receipts, persisted = setup_case(monkeypatch)
    receipts.return_value = "recovery_effect_unconfirmed"
    with pytest.raises(RecoveryCheckpointBlocked, match="recovery_effect_unconfirmed"):
        await recovery.resolve_recovery_checkpoint(AsyncMock(), run, cfg)
    persisted.assert_not_awaited()


async def test_rejects_prior_checkpoint_even_on_same_thread(monkeypatch):
    cfg, run, saver, _, receipts, persisted = setup_case(monkeypatch)
    saved = deepcopy(saver.aget_tuple.return_value)
    saved.metadata["moldy_run_id"] = "other"
    saver.aget_tuple.return_value = saved
    with pytest.raises(RecoveryCheckpointBlocked, match="recovery_checkpoint_unavailable"):
        await recovery.resolve_recovery_checkpoint(AsyncMock(), run, cfg)
    receipts.assert_not_awaited()
    persisted.assert_not_awaited()


@pytest.mark.parametrize(
    ("client_id", "reuse_existing_id"),
    [(None, False), ("message-from-client", False), ("message-from-client", True)],
)
async def test_enrolled_chat_dict_preserves_user_identity_at_graph_entry(
    monkeypatch, client_id, reuse_existing_id
):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, MessagesState, StateGraph

    from app.agent_runtime import langgraph_agent_stream_runner as runner
    from app.agent_runtime.run_recovery_checkpoint import turn_tools
    from app.services.conversation_run_fence import run_fence_scope

    fence, cfg, config = context()
    graph = StateGraph(MessagesState)
    graph.add_node("answer", lambda state: {"messages": [AIMessage(content="done")]})
    graph.add_edge(START, "answer")
    graph.add_edge("answer", END)
    agent = graph.compile(checkpointer=InMemorySaver())
    monkeypatch.setattr(runner, "_prepare_agent", AsyncMock(return_value=(agent, [], config)))
    if reuse_existing_id:
        await agent.ainvoke({"messages": [HumanMessage(id=client_id, content="old text")]}, config)
    raw = {"role": "user", "content": "existing wire message"}
    if client_id:
        raw["id"] = client_id
    with run_fence_scope(fence):
        chunks = [
            chunk
            async for chunk in runner.execute_agent_stream_langgraph(
                cfg, {"messages": [raw]}, run_id=str(fence.run_id), persist_callback=AsyncMock()
            )
        ]
    assert chunks
    latest = await agent.aget_state(config)
    messages = latest.values["messages"]
    users = [message for message in messages if isinstance(message, HumanMessage)]
    assert len(users) == 1
    user, answer = users[0], messages[-1]
    assert user.id == (client_id or str(uuid.uuid5(fence.run_id, "chat-recovery-input")))
    assert user.content == raw["content"] and answer.content == "done"
    assert "response_metadata" not in raw
    if reuse_existing_id:
        with pytest.raises(RecoveryCheckpointBlocked):
            turn_tools(messages, fence.run_id)
    else:
        assert turn_tools(messages, fence.run_id).requested == frozenset()
