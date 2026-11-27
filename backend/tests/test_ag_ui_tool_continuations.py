"""Recover a tool's original public identity across an admitted human approval."""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import checkpointer
from app.agent_runtime.ag_ui_v1_issuance import ToolContinuation, public_tool_call_id
from app.agent_runtime.langgraph_lifecycle_events import lifecycle_protocol_event
from app.agent_runtime.protocol_events import stored_protocol_event
from app.approvals import service as approvals
from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.auth.jwt import create_access_token
from app.authz.org_context import OrgContext
from app.config import settings
from app.exceptions import NotFoundError
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.approval_request import ApprovalRequest
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from app.services.ag_ui_tool_continuations import read_tool_continuations
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.agent_execution_runtime_fixture import seed_runtime_run
from tests.authz.queue_resource_fixture import queue_resource_sql
from tests.authz.unit_scope_fixture import unit_context

pytestmark = pytest.mark.asyncio

ISSUER = "assistant-before-approval"
RAW_ID = "provider-call-7"
TOOL = "lookup"
PARENT_PUBLIC = "original/turn"
RESUMED_PUBLIC = "approved/turn"
ARGS = {"topic": "meeting notes"}
PROPOSED = AIMessage(
    id=ISSUER,
    content="",
    tool_calls=[{"id": RAW_ID, "name": TOOL, "args": ARGS}],
)
REVIEW = {
    "action_requests": [{"name": TOOL, "args": ARGS}],
    "review_configs": [{"action_name": TOOL, "allowed_decisions": ["approve"]}],
}


def _approval_node(state: MessagesState) -> dict[str, list[ToolMessage]]:
    assert state["messages"][-1].id == ISSUER
    if interrupt(REVIEW) != "approve":
        raise AssertionError("This case expects an approved read")
    return {"messages": [ToolMessage(name=TOOL, tool_call_id=RAW_ID, content="Meeting at noon")]}


@dataclass
class ApprovedTurn:
    db: AsyncSession
    room: Conversation
    context: OrgContext
    parent: ConversationRun
    child: ConversationRun
    pin: str
    child_head: str
    trace: MessageEvent
    result: ToolMessage

    async def read(
        self, *, checkpoint_id: str | None, context: OrgContext | None = None
    ) -> tuple[ToolContinuation, ...]:
        return await read_tool_continuations(
            self.db,
            conversation=self.room,
            context=context or self.context,
            run=self.child,
            checkpoint_id=checkpoint_id,
        )


@pytest.fixture
async def approved(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> ApprovedTurn:
    seeded, _, room = await seed_runtime_run()
    parent, room = await db.merge(seeded), await db.merge(room)
    context = unit_context(parent.user_id)
    saver = InMemorySaver()
    monkeypatch.setattr(checkpointer, "get_checkpointer", lambda: saver)
    chat = (
        StateGraph(MessagesState)
        .add_node("propose", lambda state: {"messages": [PROPOSED]})
        .add_node("review", _approval_node)
        .add_edge(START, "propose")
        .add_edge("propose", "review")
        .add_edge("review", END)
        .compile(checkpointer=saver)
    )
    parent_config: RunnableConfig = {
        "configurable": run_configurable(str(room.id), parent.id, context.tenant_id)
    }
    paused = await chat.ainvoke(
        {"messages": [HumanMessage(content="Find the meeting notes")]}, parent_config
    )
    [native] = paused["__interrupt__"]
    saved = await saver.aget_tuple(parent_config)
    assert saved is not None
    pin = saved.checkpoint["id"]
    parent.status, parent.is_active, parent.interrupt_id = "interrupted", False, native.id
    await db.flush()

    child = ConversationRun(
        conversation_id=room.id,
        agent_id=parent.agent_id,
        tenant_id=context.tenant_id,
        org_id=context.org_id,
        user_id=context.user_id,
        source="resume",
        run_source="CHAT",
        invoker_type="user",
        parent_run_id=parent.id,
        interrupt_id=native.id,
        status="completed",
        is_active=False,
    )
    db.add(child)
    await db.flush()
    for run_id, public_id, checkpoint in (
        (parent.id, PARENT_PUBLIC, None),
        (child.id, RESUMED_PUBLIC, pin),
    ):
        db.add(
            AgUiCommandReceipt(
                conversation_id=room.id,
                run_id=run_id,
                public_run_id=public_id,
                projection_version="1",
                checkpoint_id=checkpoint,
                tenant_id=context.tenant_id,
                org_id=context.org_id,
                user_id=context.user_id,
                request_digest="0" * 64,
            )
        )
    await approvals.record_pending(
        db,
        parent,
        [approvals.PendingInterrupt(native.id, REVIEW)],
        origin=approvals.RunOrigin(agent_name="Runtime Agent", source="chat"),
    )
    [claimed] = await approvals.claim(
        db,
        approvals.Claim(
            run_id=parent.id,
            decisions={native.id: ["approve"]},
            channel="chat",
            actor_id=context.user_id,
            resumed_run_id=child.id,
        ),
    )
    assert claimed.status == "approved"
    trace = MessageEvent(
        conversation_id=room.id,
        assistant_msg_id=str(parent.id),
        status="completed",
        events=[
            stored_protocol_event(
                run_id=str(parent.id),
                thread_id=str(room.id),
                seq=1,
                method="messages",
                data=PROPOSED.model_dump(mode="json"),
            ),
            stored_protocol_event(
                run_id=str(parent.id),
                thread_id=str(room.id),
                seq=2,
                checkpoint_id=pin,
                method="input.requested",
                data={"interrupt_id": native.id, "payload": REVIEW, "namespace": []},
            ),
            lifecycle_protocol_event(
                run_id=str(parent.id),
                thread_id=str(room.id),
                seq=3,
                event="interrupted",
            ),
        ],
    )
    db.add(trace)
    await db.commit()
    child_config: RunnableConfig = {
        "configurable": run_configurable(str(room.id), child.id, context.tenant_id)
    }
    resumed = await chat.ainvoke(Command(resume={native.id: "approve"}), child_config)
    result = resumed["messages"][-1]
    assert isinstance(result, ToolMessage) and result.tool_call_id == RAW_ID
    new_head = await saver.aget_tuple(child_config)
    assert new_head is not None and new_head.checkpoint["id"] != pin
    return ApprovedTurn(
        db, room, context, parent, child, pin, new_head.checkpoint["id"], trace, result
    )


async def test_approved_tool_keeps_issuing_run_after_the_graph_advances(
    approved: ApprovedTurn,
) -> None:
    with queue_resource_sql(approved.db) as statements:
        actual = await approved.read(checkpoint_id=approved.pin)
    assert statements and all(
        sql.lstrip().upper().startswith(("SELECT", "WITH")) for sql in statements
    )
    [call] = actual
    assert (call.owner, call.raw_id, call.name, call.parent_message_id) == (
        None,
        RAW_ID,
        TOOL,
        ISSUER,
    )
    assert call.public_id == public_tool_call_id(PARENT_PUBLIC, None, ISSUER, RAW_ID)
    assert all("delivery_encrypted" not in statement for statement in statements)


async def test_native_unreceipted_resume_reuses_only_its_parent_checkpoint_edge(
    approved: ApprovedTurn,
) -> None:
    await approved.db.execute(
        delete(AgUiCommandReceipt).where(AgUiCommandReceipt.run_id == approved.child.id)
    )
    await approved.db.commit()
    [call] = await approved.read(checkpoint_id=None)
    assert call.public_id == public_tool_call_id(PARENT_PUBLIC, None, ISSUER, RAW_ID)
    assert not await approved.read(checkpoint_id=approved.child_head)


async def test_continuation_requires_the_accepted_checkpoint_and_parent_status(
    approved: ApprovedTurn,
) -> None:
    assert not await approved.read(checkpoint_id=approved.child_head)
    approved.parent.status = "completed"
    await approved.db.commit()
    assert not await approved.read(checkpoint_id=approved.pin)


async def test_prior_tool_identity_is_private_to_its_original_actor_and_company(
    approved: ApprovedTurn,
) -> None:
    context = approved.context
    for actor, org in ((uuid4(), context.org_id), (context.user_id, uuid4())):
        foreign = OrgContext(user_id=actor, org_id=org, tenant_id=context.tenant_id)
        with pytest.raises(NotFoundError) as denied:
            await approved.read(checkpoint_id=approved.pin, context=foreign)
        assert denied.value.status == 404


async def test_fresh_send_and_missing_proposal_never_recover_a_historical_call(
    approved: ApprovedTurn,
) -> None:
    approved.child.source = "start"
    await approved.db.commit()
    assert not await approved.read(checkpoint_id=approved.pin)
    approved.child.source = "resume"
    approved.trace.events = list(approved.trace.events[1:])
    await approved.db.commit()
    assert not await approved.read(checkpoint_id=approved.pin)


@pytest.mark.parametrize("removed_method", ["input.requested", "lifecycle"])
async def test_truncated_predecessor_journal_cannot_authorize_its_call(
    approved: ApprovedTurn, removed_method: str
) -> None:
    approved.trace.events = [
        event
        for event in approved.trace.events
        if isinstance(event, dict) and event.get("method") != removed_method
    ]
    await approved.db.commit()
    assert not await approved.read(checkpoint_id=approved.pin)


async def test_resumed_result_rejoins_original_call_on_public_cold_stream(
    approved: ApprovedTurn, raw_client: AsyncClient
) -> None:
    run_id, thread_id = str(approved.child.id), str(approved.room.id)
    approved.db.add(
        MessageEvent(
            conversation_id=approved.room.id,
            assistant_msg_id=run_id,
            status="completed",
            events=[
                stored_protocol_event(
                    run_id=run_id,
                    thread_id=thread_id,
                    seq=1,
                    method="messages",
                    data=approved.result.model_dump(mode="json"),
                ),
                lifecycle_protocol_event(
                    run_id=run_id, thread_id=thread_id, seq=2, event="completed"
                ),
            ],
        )
    )
    await approved.db.commit()
    raw_client.cookies.set(
        settings.cookie_name_access, create_access_token(approved.context.user_id)
    )
    raw_client.cookies.set(ACTIVE_ORG_COOKIE, str(approved.context.org_id))
    url = f"/api/conversations/{thread_id}/ag-ui/stream"
    parent = await raw_client.get(url, params={"runId": PARENT_PUBLIC})
    following = await raw_client.get(url, params={"runId": RESUMED_PUBLIC})
    assert parent.status_code == following.status_code == 200
    issued = [
        json.loads(line[6:]) for line in parent.text.splitlines() if line.startswith("data: ")
    ]
    received = [
        json.loads(line[6:]) for line in following.text.splitlines() if line.startswith("data: ")
    ]
    [call] = [event for event in issued if event["type"] == "TOOL_CALL_START"]
    results = [event for event in received if event["type"] == "TOOL_CALL_RESULT"]
    assert len(results) == 1, received
    [result] = results
    assert received[0]["type"] == "RUN_STARTED"
    assert received[0]["runId"] == RESUMED_PUBLIC
    assert "parentRunId" not in received[0]
    assert received[-1]["type"] == "RUN_FINISHED"
    assert (
        call["toolCallId"]
        == result["toolCallId"]
        == public_tool_call_id(PARENT_PUBLIC, None, ISSUER, RAW_ID)
    )
    assert result["content"] == approved.result.content


async def test_approval_claim_for_a_different_physical_run_cannot_carry_the_call(
    approved: ApprovedTurn,
) -> None:
    await approved.db.execute(
        update(ApprovalRequest)
        .where(ApprovalRequest.run_id == approved.parent.id)
        .values(resumed_run_id=approved.parent.id)
    )
    await approved.db.commit()
    assert not await approved.read(checkpoint_id=approved.pin)
