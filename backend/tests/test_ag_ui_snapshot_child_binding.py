"""An ordinary chat may remain readable after its managed child is revoked."""

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import ag_ui_v1_issuance, checkpointer
from app.agent_runtime.protocol_events import stored_protocol_event
from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.auth.jwt import create_access_token
from app.child_tasks.persistence import begin_child_task
from app.config import settings
from app.models.agent_subagent import AgentSubAgentLink
from app.models.message_event import MessageEvent, MessageEventChunk
from app.services import trace_storage
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.test_child_task_authorization import ChildCase
from tests.authz.test_child_task_authorization import child_case as child_case


@pytest.mark.asyncio
@pytest.mark.parametrize("expire_trace", [False, True], ids=["journal", "checkpoint-only"])
async def test_snapshot_removes_child_handle_and_messages_when_link_is_revoked(
    raw_client: AsyncClient,
    db: AsyncSession,
    child_case: ChildCase,
    monkeypatch: pytest.MonkeyPatch,
    expire_trace: bool,
) -> None:
    graph = child_case.graph
    ctx = graph.context
    conversation_id = graph.conversation_ids[0]
    call_id = "call-research"
    namespace = ["tools:research"]
    task_id = await begin_child_task(
        db,
        context=ctx,
        conversation_id=conversation_id,
        run_id=child_case.run_id,
        tool_call_id=call_id,
        child_agent_id=child_case.child_id,
        role="researcher",
        checkpoint_ns=namespace[0],
    )
    assert task_id is not None
    await db.commit()

    parent = AIMessage(
        id="parent-message",
        content="I will check.",
        tool_calls=[{"type": "tool_call", "id": call_id, "name": "task", "args": {}}],
    )
    tool_result = ToolMessage(
        id="task-result", name="task", tool_call_id=call_id, content="Verified child findings."
    )
    saver = InMemorySaver()
    monkeypatch.setattr(checkpointer, "_checkpointer", saver)

    def delegate(state: MessagesState) -> dict[str, list[AIMessage | ToolMessage]]:
        assert state["messages"][-1].id == "question"
        return {"messages": [parent, tool_result]}

    chat = (
        StateGraph(MessagesState)
        .add_node("delegate", delegate)
        .add_edge(START, "delegate")
        .add_edge("delegate", END)
        .compile(checkpointer=saver)
    )
    await chat.ainvoke(
        {"messages": [HumanMessage(id="question", content="Check the entries.")]},
        {"configurable": run_configurable(str(conversation_id), child_case.run_id, ctx.tenant_id)},
    )
    lifecycle = {
        "event": "started",
        "namespace": namespace,
        "graph_name": "researcher",
        "cause": {"type": "toolCall", "tool_call_id": call_id},
    }
    child_text = "The child found three entries."
    child = {"id": "child-message", "type": "ai", "content": child_text}
    frames = [
        stored_protocol_event(
            run_id=str(child_case.run_id),
            thread_id=str(conversation_id),
            seq=index,
            method=method,
            namespace=owner,
            data=data,
        )
        for index, (method, owner, data) in enumerate(
            [
                ("messages", [], parent.model_dump()),
                ("lifecycle", [], lifecycle),
                ("messages", namespace, child),
                ("messages", [], tool_result.model_dump()),
            ],
            start=1,
        )
    ]
    await trace_storage.append_events(
        db,
        conversation_id=conversation_id,
        assistant_msg_id=str(child_case.run_id),
        events_chunk=[dict(frame) for frame in frames],
    )
    await db.commit()
    raw_client.cookies.set(settings.cookie_name_access, create_access_token(ctx.user_id))
    raw_client.cookies.set(ACTIVE_ORG_COOKIE, str(ctx.org_id))
    endpoint = f"/api/conversations/{conversation_id}/ag-ui/snapshot"
    params = {"runId": str(child_case.run_id)}

    before = await raw_client.get(endpoint, params=params)
    assert before.status_code == 200, before.text
    [admitted] = before.json()["subagents"]
    assert admitted["childTaskId"] == str(task_id)
    public_call_id = ag_ui_v1_issuance.public_tool_call_id(
        str(child_case.run_id), None, parent.id, call_id
    )
    assert public_call_id != call_id and call_id not in before.text
    issuer = next(row for row in before.json()["messages"] if row["id"] == parent.id)
    assert issuer["toolCalls"][0]["id"] == public_call_id
    assert admitted["parentToolCallId"] == public_call_id
    assert admitted["parentMessageId"] == parent.id
    public_id = admitted["subagentRunId"]
    assert public_id != str(task_id)
    assert any(
        message.get("subagentRunId") == public_id and message["content"] == child_text
        for message in before.json()["messages"]
    )
    assert any(
        message.get("toolCallId") == public_call_id and message["content"] == tool_result.text
        for message in before.json()["messages"]
    )

    await db.execute(
        delete(AgentSubAgentLink).where(
            AgentSubAgentLink.parent_agent_id == graph.agent_id,
            AgentSubAgentLink.sub_agent_id == child_case.child_id,
        )
    )
    if expire_trace:
        for model in (MessageEventChunk, MessageEvent):
            await db.execute(
                delete(model).where(
                    model.conversation_id == conversation_id,
                    model.assistant_msg_id == str(child_case.run_id),
                )
            )
    await db.commit()
    after = await raw_client.get(endpoint, params=params)
    assert after.status_code == 200, after.text
    assert parent.text in after.text
    assert after.json()["subagents"] == []
    for private_value in (
        str(task_id),
        public_id,
        public_call_id,
        call_id,
        "task-result",
        child_text,
        tool_result.text,
    ):
        assert private_value not in after.text
