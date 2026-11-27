"""A managed child can recover its native approval and issuer after the journal expires."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete, select

from app.agent_runtime import ag_ui_v1_children, ag_ui_v1_issuance
from app.agent_runtime.identity import make_agent_runtime_name
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_child_task import ConversationChildTask
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from app.services.ag_ui_snapshot import load_ag_ui_snapshot
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.test_child_followup_runtime import (
    ResearchModel,
    SessionScope,
    SlowResearch,
    child_graph,
    parallel_root,
)
from tests.authz.test_child_followup_runtime import scoped_session as scoped_session
from tests.authz.test_child_task_authorization import ChildCase
from tests.authz.test_child_task_authorization import child_case as child_case


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [False, True], ids=["ordinary", "prepared"])
async def test_snapshot_recovers_only_the_currently_authorized_native_delegation(
    child_case: ChildCase,
    scoped_session: SessionScope,
    monkeypatch: pytest.MonkeyPatch,
    prepared: bool,
) -> None:
    ctx = child_case.graph.context
    thread = child_case.graph.conversation_ids[0]
    role = make_agent_runtime_name(child_case.child_id)
    saver = InMemorySaver()
    slow = SlowResearch()
    graph = child_graph(
        child_case,
        ResearchModel(responses=[]),
        slow.tool(),
        saver,
        prepared,
        root_model=parallel_root(role, ("alpha",)),
        require_approval=True,
    )
    config = run_configurable(str(thread), child_case.run_id, ctx.tenant_id)
    state = await graph.ainvoke(
        {"messages": [("user", "Research alpha.")]}, {"configurable": config}
    )
    [native] = state["__interrupt__"]
    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", lambda: saver)
    async with scoped_session(ctx) as db:
        run = await db.get(ConversationRun, child_case.run_id)
        conversation = await db.get(Conversation, thread)
        assert conversation is not None and run is not None
        run.status, run.is_active, run.interrupt_id = "interrupted", False, native.id
        await db.commit()

        waiting = await db.scalar(
            select(ConversationChildTask).where(ConversationChildTask.current_run_id == run.id)
        )
        assert waiting and waiting.status == "awaiting_approval"
        assert not await db.scalar(
            select(MessageEvent.id).where(MessageEvent.conversation_id == thread)
        )
        snapshot = await load_ag_ui_snapshot(db, conversation, ctx)

        [child] = snapshot.subagents
        [slot] = snapshot.interrupts
        owner = ag_ui_v1_children.public_subagent_id(
            str(thread), tuple(waiting.checkpoint_ns.split("|"))
        )
        assert slot.id == native.id and slot.subagent_run_id == owner == child.subagent_run_id
        assert child.child_task_id == str(waiting.id)
        assert child.interrupt_ids == [native.id] and child.status == "interrupted"
        assert child.name == role
        assert child.run_id == snapshot.interrupt_run_id == str(run.id)
        [issuer] = [msg for msg in snapshot.messages if msg.role == "assistant" and msg.tool_calls]
        assert issuer.tool_calls is not None
        [call] = issuer.tool_calls
        assert issuer.id == child.parent_message_id
        assert (
            call.id
            == child.parent_tool_call_id
            == ag_ui_v1_issuance.public_tool_call_id(
                str(run.id), None, issuer.id, waiting.tool_call_id
            )
        )
        assert call.function.name == "task"
        assert slot.response_schema
        assert not (slot.metadata or {}).get("moldy", {}).get("legacyInterruptAccess")
        await db.execute(
            delete(AgentSubAgentLink).where(
                AgentSubAgentLink.parent_agent_id == child_case.graph.agent_id,
                AgentSubAgentLink.sub_agent_id == child_case.child_id,
            )
        )
        await db.commit()

        revoked = await load_ag_ui_snapshot(db, conversation, ctx)
        assert revoked.interrupts == [] and revoked.interrupt_run_id is None
        assert revoked.subagents == []
        assert str(waiting.id) not in revoked.model_dump_json()
        assert not await db.scalar(
            select(MessageEvent.id).where(MessageEvent.conversation_id == thread)
        )
    assert not slow.started["alpha"].is_set()
