"""Run recovery against the installed graph engine and its real persisted writes."""

from __future__ import annotations

import asyncio
import copy
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import interrupt
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.recovery_stream import RecoveryStreamCursor, continue_protocol_delivery
from app.agent_runtime.run_recovery_checkpoint import (
    RecoveryCheckpointBlocked,
    bind_recoverable_input,
    checked_head,
    turn_tools,
    verified_resume_config,
)
from app.agent_runtime.runtime_config import AgentConfig
from app.authz.org_context import OrgContext
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services.conversation_run_fence import RunFence, run_fence_scope
from app.services.conversation_run_recovery_checkpoint import resolve_recovery_checkpoint
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.runtime_prototype import ScriptedModel
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import seed_agent


def context() -> tuple[RunFence, AgentConfig, RunnableConfig]:
    org = OrgContext(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    fence = RunFence(uuid.uuid4(), "worker-A", 1, uuid.uuid4(), org, uuid.uuid4())
    thread = str(fence.conversation_id)
    cfg = AgentConfig(
        provider="openai",
        model_name="test",
        api_key=None,
        base_url=None,
        system_prompt="",
        tools_config=[],
        thread_id=thread,
        org_context=org,
    )
    return fence, cfg, {"configurable": run_configurable(thread, fence.run_id, org.tenant_id)}


def enrolled_input(fence: RunFence) -> HumanMessage:
    message = HumanMessage(content="look up the record")
    with run_fence_scope(fence):
        bind_recoverable_input([message], str(fence.run_id))
    assert message.id == str(uuid.uuid5(fence.run_id, "chat-recovery-input"))
    return message


def durable_at_crash(saver: InMemorySaver) -> InMemorySaver:
    # SIGKILL leaves durable rows as they stood at the crash point. asyncio
    # cancellation alone lets LangGraph flush a later CancelledError marker.
    restarted = InMemorySaver()
    restarted.storage, restarted.blobs, restarted.writes = copy.deepcopy(
        (saver.storage, saver.blobs, saver.writes)
    )
    return restarted


class ReadSaver(InMemorySaver):
    """Observe a finished tool's real pending write in a parallel superstep."""

    def __init__(self) -> None:
        super().__init__()
        self.result_saved = asyncio.Event()

    async def aput_writes(
        self, config: Any, writes: Any, task_id: str, task_path: str = ""
    ) -> None:
        await super().aput_writes(config, writes, task_id, task_path)
        for channel, value in writes:
            if (
                channel == "messages"
                and isinstance(value, list)
                and any(isinstance(m, ToolMessage) for m in value)
            ):
                self.result_saved.set()


@pytest.mark.parametrize("crash_point", ["model", "tool_pending", "tool_committed"])
async def test_resume_current_turn_does_not_repeat_input_or_completed_read(
    crash_point: str,
) -> None:
    fence, cfg, config = context()
    saver = ReadSaver()
    blocked, release = asyncio.Event(), asyncio.Event()
    effects: list[str] = []
    call_id = "lookup-current-record"

    async def wait_at_crash_point() -> None:
        blocked.set()
        await release.wait()

    async def model(state: MessagesState) -> dict:
        if crash_point == "model":
            await wait_at_crash_point()
        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "lookup", "args": {}, "id": call_id, "type": "tool_call"}],
                )
            ]
        }

    def lookup(state: MessagesState) -> dict:
        effects.append(call_id)
        return {"messages": [ToolMessage(name="lookup", tool_call_id=call_id, content="record-3")]}

    async def parallel_task(state: MessagesState) -> dict:
        if crash_point == "tool_pending":
            await wait_at_crash_point()
        return {}

    async def answer(state: MessagesState) -> dict:
        if crash_point == "tool_committed":
            await wait_at_crash_point()
        result = next(m for m in reversed(state["messages"]) if isinstance(m, ToolMessage))
        return {"messages": [AIMessage(content=f"found {result.content}")]}

    builder = StateGraph(MessagesState)
    builder.add_node("model", model)
    builder.add_node("lookup", lookup)
    builder.add_node("parallel_task", parallel_task)
    builder.add_node("answer", answer)
    builder.add_edge(START, "model")
    builder.add_edge("model", "lookup")
    builder.add_edge("model", "parallel_task")
    builder.add_edge(["lookup", "parallel_task"], "answer")
    builder.add_edge("answer", END)
    graph = builder.compile(checkpointer=saver)
    runner = asyncio.create_task(
        graph.ainvoke({"messages": [enrolled_input(fence)]}, config, durability="sync")
    )
    try:
        await asyncio.wait_for(blocked.wait(), timeout=10)
        if crash_point == "tool_pending":
            await asyncio.wait_for(saver.result_saved.wait(), timeout=10)
        head = await saver.aget_tuple(config)
        checkpoint_id = checked_head(head, cfg, fence.run_id)
        if crash_point == "tool_pending":
            assert head is not None
            assert any(channel == "messages" for _, channel, _ in head.pending_writes or ())
        assert effects == ([] if crash_point == "model" else [call_id])
        restarted_saver = durable_at_crash(saver)
    finally:
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner
    release.set()
    graph = builder.compile(checkpointer=restarted_saver)
    second_owner = replace(fence, owner_token=uuid.uuid4(), generation=2, worker_instance_id="B")
    cursor = RecoveryStreamCursor.from_events(str(fence.run_id), cfg.thread_id, [])
    exact = {"configurable": {**config.get("configurable", {}), "checkpoint_id": checkpoint_id}}
    with run_fence_scope(second_owner), continue_protocol_delivery(cursor):
        current = await verified_resume_config(
            graph, exact, replace(cfg, checkpoint_id=checkpoint_id), str(fence.run_id)
        )
        assert "checkpoint_id" not in current["configurable"]
        resumed = await graph.ainvoke(None, cast(RunnableConfig, current), durability="sync")
    messages = resumed["messages"]
    assert [message.id for message in messages if isinstance(message, HumanMessage)] == [
        str(uuid.uuid5(fence.run_id, "chat-recovery-input"))
    ]
    assert effects == [call_id]
    assert [message.tool_call_id for message in messages if isinstance(message, ToolMessage)] == [
        call_id
    ]
    assert messages[-1].content == "found record-3"


async def test_approval_checkpoint_is_never_automatically_replayed() -> None:
    fence, cfg, config = context()
    saver = InMemorySaver()

    def approval(state: MessagesState) -> dict:
        interrupt("approve the action")
        return {"messages": [AIMessage(content="approved")]}

    builder = StateGraph(MessagesState)
    builder.add_node("approval", approval)
    builder.add_edge(START, "approval")
    builder.add_edge("approval", END)
    graph = builder.compile(checkpointer=saver)
    await graph.ainvoke({"messages": [enrolled_input(fence)]}, config)
    head = await saver.aget_tuple(config)
    assert head is not None
    assert any(channel == "__interrupt__" for _, channel, _ in head.pending_writes or ())
    checkpoint_id = head.checkpoint["id"]
    with pytest.raises(RecoveryCheckpointBlocked):
        checked_head(head, cfg, fence.run_id)
    exact = {"configurable": {**config.get("configurable", {}), "checkpoint_id": checkpoint_id}}
    with run_fence_scope(fence), pytest.raises(RecoveryCheckpointBlocked):
        await verified_resume_config(
            graph, exact, replace(cfg, checkpoint_id=checkpoint_id), str(fence.run_id)
        )


def test_requested_and_completed_tools_are_bound_to_the_current_user_turn() -> None:
    fence, _, _ = context()
    old_run = replace(fence, run_id=uuid.uuid4())
    first = enrolled_input(old_run)
    second = enrolled_input(fence)
    first_call = AIMessage(content="", tool_calls=[{"name": "lookup", "args": {}, "id": "old"}])
    first_result = ToolMessage(content="prior result", tool_call_id="old", name="lookup")
    current_call = AIMessage(content="", tool_calls=[{"name": "lookup", "args": {}, "id": "new"}])
    messages = [first, first_call, first_result, second, current_call]
    result = turn_tools(messages, fence.run_id)
    assert result.requested == {"new"}
    assert result.completed == set()
    with pytest.raises(RecoveryCheckpointBlocked):
        turn_tools(messages, old_run.run_id)
    with pytest.raises(RecoveryCheckpointBlocked):
        turn_tools([*messages, first_result], fence.run_id)


async def test_run_tenant_and_head_must_all_match_the_current_graph() -> None:
    fence, cfg, config = context()
    saver = InMemorySaver()
    builder = StateGraph(MessagesState)
    builder.add_node("model", lambda state: {"messages": [AIMessage(content="ok")]})
    builder.add_edge(START, "model")
    builder.add_edge("model", END)
    graph = builder.compile(checkpointer=saver, interrupt_before=["model"])
    await graph.ainvoke({"messages": [enrolled_input(fence)]}, config, durability="sync")
    saved = await saver.aget_tuple(config)
    checkpoint_id = checked_head(saved, cfg, fence.run_id)
    cursor = RecoveryStreamCursor.from_events(str(fence.run_id), cfg.thread_id, [])
    exact = {"configurable": {**config.get("configurable", {}), "checkpoint_id": checkpoint_id}}
    cfg = replace(cfg, checkpoint_id=checkpoint_id)
    with run_fence_scope(fence), continue_protocol_delivery(cursor):
        checked = await verified_resume_config(graph, exact, cfg, str(fence.run_id))
        assert checked["configurable"] == config.get("configurable", {})

    with pytest.raises(RecoveryCheckpointBlocked):
        checked_head(saved, cfg, uuid.uuid4())
    other_tenant = replace(fence.tenant, tenant_id=uuid.uuid4())
    with pytest.raises(RecoveryCheckpointBlocked):
        checked_head(saved, replace(cfg, org_context=other_tenant), fence.run_id)
    with pytest.raises(RecoveryCheckpointBlocked):
        checked_head(saved, replace(cfg, thread_id=str(uuid.uuid4())), fence.run_id)
    with (
        run_fence_scope(replace(fence, tenant=other_tenant)),
        pytest.raises(RecoveryCheckpointBlocked),
    ):
        await verified_resume_config(graph, exact, cfg, str(fence.run_id))
    obsolete_id = str(uuid.uuid4())
    obsolete = {"configurable": {**config.get("configurable", {}), "checkpoint_id": obsolete_id}}
    with run_fence_scope(fence), pytest.raises(RecoveryCheckpointBlocked):
        await verified_resume_config(
            graph, obsolete, replace(cfg, checkpoint_id=obsolete_id), str(fence.run_id)
        )


async def test_recompiled_deep_agent_consumes_saved_tool_result_once(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, _model, db_agent = await seed_agent(db)
    scope = unit_context(user.id)
    fence, cfg, _config = context()
    fence = replace(fence, tenant=scope)
    cfg = replace(cfg, org_context=scope, agent_id=str(db_agent.id), user_id=str(user.id))
    config: RunnableConfig = {
        "configurable": run_configurable(cfg.thread_id, fence.run_id, scope.tenant_id)
    }
    conversation = Conversation(
        id=fence.conversation_id,
        org_id=scope.org_id,
        tenant_id=scope.tenant_id,
        user_id=user.id,
        agent_id=db_agent.id,
        agent_name_snapshot=db_agent.name,
        last_activity_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(conversation)
    await db.flush()
    run = ConversationRun(
        id=fence.run_id,
        org_id=scope.org_id,
        tenant_id=scope.tenant_id,
        user_id=user.id,
        agent_id=db_agent.id,
        conversation_id=conversation.id,
        source="chat",
        status="running",
        is_active=True,
    )
    db.add(run)
    await db.flush()
    call_id = "call-record"
    tool_results: list[str] = []
    resumed_results: list[str] = []
    waiting = asyncio.Event()

    @tool
    def record_lookup() -> str:
        """Return the existing record's identifier."""
        tool_results.append("record-3")
        return "record-3"

    class LookupModel(ScriptedModel):
        async def _agenerate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
            last = messages[-1]
            if isinstance(last, ToolMessage):
                assert last.tool_call_id == call_id
                assert isinstance(last.content, str) and last.content == "record-3"
                if not waiting.is_set():
                    waiting.set()
                    await asyncio.Event().wait()
                resumed_results.append(last.content)
                response = AIMessage(content=f"found {last.content}")
            else:
                response = AIMessage(
                    content="", tool_calls=[{"name": "record_lookup", "args": {}, "id": call_id}]
                )
            return ChatResult(generations=[ChatGeneration(message=response)])

    def agent(saver: InMemorySaver) -> Any:
        return create_deep_agent(
            model=LookupModel(responses=[]),
            tools=[record_lookup],
            system_prompt="Find the existing record.",
            checkpointer=saver,
        )

    saver = InMemorySaver()
    graph = agent(saver)
    runner = asyncio.create_task(
        graph.ainvoke({"messages": [enrolled_input(fence)]}, config, durability="sync")
    )
    try:
        await asyncio.wait_for(waiting.wait(), timeout=15)
        checkpoint_id = checked_head(await saver.aget_tuple(config), cfg, fence.run_id)
        saved = durable_at_crash(saver)
    finally:
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner
    new_owner = replace(fence, generation=2, owner_token=uuid.uuid4(), worker_instance_id="B")
    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", lambda: saved)
    assert await resolve_recovery_checkpoint(db, run, cfg) == checkpoint_id
    recovered = agent(saved)
    exact = {"configurable": {**config.get("configurable", {}), "checkpoint_id": checkpoint_id}}
    cursor = RecoveryStreamCursor.from_events(str(fence.run_id), cfg.thread_id, [])
    with run_fence_scope(new_owner), continue_protocol_delivery(cursor):
        current = await verified_resume_config(
            recovered, exact, replace(cfg, checkpoint_id=checkpoint_id), str(fence.run_id)
        )
        final = await recovered.ainvoke(None, current)
    assert tool_results == resumed_results == ["record-3"]
    assert len([m for m in final["messages"] if isinstance(m, HumanMessage)]) == 1
    assert len([m for m in final["messages"] if isinstance(m, ToolMessage)]) == 1
    assert final["messages"][-1].content == "found record-3"
