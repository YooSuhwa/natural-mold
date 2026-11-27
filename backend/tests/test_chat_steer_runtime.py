from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import chat_steer_middleware as steer
from app.agent_runtime.chat_steer_checkpoint import confirmed_steer_ids
from app.authz.conversation_scope import require_conversation_agent_id
from app.models.conversation import Conversation
from app.services import conversation_run_service
from app.services.conversation_run_steers import CONVERSATION_STEERS, admit_steer
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent
from tests.sqlite_test_engine import TestSession


async def test_two_inflight_steers_are_delivered_in_order_and_survive_compaction(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(steer, "tenant_session", lambda _: TestSession())
    context = unit_context(TEST_USER_ID)
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=require_conversation_agent_id(conversation),
        context=context,
        source="chat",
        input_preview="find suppliers",
    )
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="test")
    await db.commit()
    started = asyncio.Event()
    finish = asyncio.Event()
    inputs: list[list[BaseMessage]] = []
    tool_calls: list[str] = []

    class InspectModel(FakeMessagesListChatModel):
        def bind_tools(self, tools: Any, **kwargs: Any) -> InspectModel:
            return self

        def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:
            inputs.append([m for m in messages if m.type != "system"])
            return super()._generate(messages, *args, **kwargs)

    @tool
    async def find_suppliers() -> str:
        """Return the suppliers after the test has accepted both steers."""
        tool_calls.append("find_suppliers")
        started.set()
        await asyncio.wait_for(finish.wait(), 20)
        return "public supplier sources"

    saver = InMemorySaver()
    model = InspectModel(
        responses=[
            AIMessage(
                content="", tool_calls=[{"id": "call-1", "name": "find_suppliers", "args": {}}]
            ),
            AIMessage(content="done"),
        ]
    )
    graph = create_deep_agent(
        model=model,
        tools=[find_suppliers],
        middleware=[steer.RunSteerMiddleware(CONVERSATION_STEERS)],
        checkpointer=saver,
    )
    config: RunnableConfig = {
        "configurable": run_configurable(str(conversation_id), run.id, context.tenant_id)
    }
    task = asyncio.create_task(
        graph.ainvoke({"messages": [HumanMessage(content="find suppliers")]}, config)
    )
    try:
        await asyncio.wait_for(started.wait(), 20)
        admissions = []
        for text in ("use Korean sources", "prioritize public suppliers"):
            row = await admit_steer(
                db,
                conversation_id=conversation_id,
                run_id=run.id,
                context=context,
                client_request_id=uuid4(),
                text=text,
            )
            admissions.append(row)
        await db.commit()
        assert all(row.status == "pending" for row in admissions)
        finish.set()
        await asyncio.wait_for(task, 20)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert [[m.type for m in call] for call in inputs] == [
        ["human"],
        ["human", "ai", "tool", "human", "human"],
    ]
    assert [m.content for m in inputs[1][-3:]] == [
        "public supplier sources",
        "use Korean sources",
        "prioritize public suppliers",
    ]
    assert tool_calls == ["find_suppliers"]
    ids = [row.id for row in admissions]
    assert [m.id for m in inputs[1][-2:]] == [steer.steer_message_id(i) for i in ids]
    assert await confirmed_steer_ids(
        conversation_id,
        run.id,
        ids,
        saver=saver,
        tenant_id=context.tenant_id,
    ) == set(ids)
    for row in admissions:
        await db.refresh(row)
        assert row.status == "applied" and row.applied_at is not None
    # Reopen the saved graph after dropping the previous model context. Removing
    # message bodies must not remove the independent durable delivery receipts.
    await graph.aupdate_state(
        config,
        {"messages": [RemoveMessage(id=steer.steer_message_id(i)) for i in ids]},
    )
    fresh = create_deep_agent(
        model=InspectModel(responses=[AIMessage(content="reopened")]),
        middleware=[steer.RunSteerMiddleware(CONVERSATION_STEERS)],
        checkpointer=saver,
    )
    await fresh.ainvoke({"messages": [HumanMessage(content="resume work")]}, config)
    assert not {m.id for m in inputs[-1]}.intersection(steer.steer_message_id(i) for i in ids)
    assert await confirmed_steer_ids(
        conversation_id,
        run.id,
        ids,
        saver=saver,
        tenant_id=context.tenant_id,
    ) == set(ids)


def test_directives_wait_for_all_sibling_tool_results() -> None:
    calls = AIMessage(
        content="",
        tool_calls=[
            {"id": "one", "name": "search", "args": {}},
            {"id": "two", "name": "search", "args": {}},
        ],
    )
    first = ToolMessage(content="a", tool_call_id="one")
    second = ToolMessage(content="b", tool_call_id="two")
    assert not steer.tool_results_complete([calls])
    assert not steer.tool_results_complete([calls, second])
    assert steer.tool_results_complete([calls, second, first])
