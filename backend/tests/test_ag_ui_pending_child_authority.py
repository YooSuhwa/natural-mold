"""A suspended delegation still needs its original participant and live runner permission."""

from __future__ import annotations

from uuid import UUID

import pytest
from ag_ui.core import ResumeEntry
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.identity import make_agent_runtime_name
from app.authz import child_task_authorization
from app.authz.execution_transaction_tracker import current_state
from app.authz.organization_execution_gateway import acquire_execution_proof
from app.authz.organization_execution_proof import live_proof
from app.child_tasks.persistence import begin_child_task
from app.exceptions import AppError
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_child_task import ConversationChildTask
from app.models.conversation_run import ConversationRun
from app.routers.conversation_agent_protocol_interrupts import ThreadInterrupt
from app.services import ag_ui_resume, child_tasks_api
from app.services.ag_ui_pending_checkpoint import PendingCheckpoint, read_pending_checkpoint
from app.services.ag_ui_pending_child_authority import (
    read_pending_child_authority,
    require_pending_child_authority,
)
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.authz_service_testkit import RecordingClient, service
from tests.authz.queue_resource_fixture import queue_resource_sql
from tests.authz.test_child_followup_runtime import (
    ResearchModel,
    SessionScope,
    SlowResearch,
    child_graph,
    parallel_root,
)
from tests.authz.test_child_followup_runtime import scoped_session as scoped_session
from tests.authz.test_child_task_authorization import ChildCase, _share_child
from tests.authz.test_child_task_authorization import child_case as child_case
from tests.test_conversation_child_tasks_router import Delegation
from tests.test_conversation_child_tasks_router import delegation as delegation

pytestmark = pytest.mark.asyncio


def _slot(slot_id: str, ns: list[str]) -> ThreadInterrupt:
    return {
        "id": slot_id,
        "ns": ns,
        "value": {
            "action_requests": [{"name": "send_reply", "args": {"message": "ok"}}],
            "review_configs": [
                {"action_name": "send_reply", "allowed_decisions": ["approve", "reject"]}
            ],
        },
    }


async def _suspend(
    db: AsyncSession, target: Delegation
) -> tuple[Conversation, ConversationRun, list[str], list[str]]:
    conversation = await db.get(Conversation, target.conversation_id)
    parent = await db.get(ConversationRun, target.run_id)
    assert conversation and parent
    parent.status, parent.is_active, parent.interrupt_id = "interrupted", False, "child-review"

    async def suspend_task(task_id: UUID) -> list[str]:
        task = await db.get(ConversationChildTask, task_id)
        assert task
        task.status, task.checkpoint_id = "awaiting_approval", f"child-checkpoint-{task.id}"
        return [*task.checkpoint_ns.split("|"), "model:review-step"]

    researcher = await suspend_task(target.child_id)
    writer = await suspend_task(target.sibling_id)
    await db.commit()
    return conversation, parent, researcher, writer


def _pending(*slots: ThreadInterrupt) -> PendingCheckpoint:
    return PendingCheckpoint(
        "current-root-checkpoint", list(slots), {}, {slot["id"]: "approval" for slot in slots}
    )


async def test_mixed_root_and_two_children_share_the_locked_authority_prefix(
    db: AsyncSession, delegation: Delegation
) -> None:
    conversation, parent, researcher, writer = await _suspend(db, delegation)
    assert conversation.agent_id
    await db.refresh(parent)
    await acquire_execution_proof(
        db,
        delegation.context,
        root_agent_id=conversation.agent_id,
        conversation_ids=(conversation.id,),
    )
    pending = _pending(_slot("root", []), _slot("research", researcher), _slot("write", writer))
    await require_pending_child_authority(
        db,
        conversation=conversation,
        context=delegation.context,
        parent_run=parent,
        pending=pending,
    )
    for task_id in (delegation.child_id, delegation.sibling_id):
        task = await db.get(ConversationChildTask, task_id)
        assert task and task.status == "awaiting_approval"
    assert parent.status == "interrupted"


@pytest.mark.parametrize("journal_namespace", ["retained", "absent"])
@pytest.mark.parametrize("abandoned", [False, True], ids=["resolve", "cancel"])
async def test_delegation_revoked_after_display_rejects_the_whole_batch(
    db: AsyncSession,
    delegation: Delegation,
    monkeypatch: pytest.MonkeyPatch,
    journal_namespace: str,
    abandoned: bool,
) -> None:
    conversation, parent, researcher, writer = await _suspend(db, delegation)
    child_slot = _slot("child-review", researcher if journal_namespace == "retained" else [])
    pending = _pending(_slot("root", []), child_slot, _slot("writer", writer))

    async def read_checkpoint(*args: object, **kwargs: object) -> PendingCheckpoint:
        return pending

    monkeypatch.setattr(ag_ui_resume, "read_pending_checkpoint", read_checkpoint)
    await db.execute(
        delete(AgentSubAgentLink).where(AgentSubAgentLink.sub_agent_id == delegation.child_agent_id)
    )
    await db.commit()
    entries = [
        ResumeEntry(interrupt_id=slot["id"], status="cancelled")
        if abandoned
        else ResumeEntry(
            interrupt_id=slot["id"], status="resolved", payload={"decisions": [{"type": "approve"}]}
        )
        for slot in pending.interrupts
    ]
    with pytest.raises(AppError) as denied:
        await ag_ui_resume.verify_ag_ui_resume(
            db, conversation=conversation, context=delegation.context, entries=entries
        )
    assert denied.value.status in {404, 409}
    assert parent.status == "interrupted"


async def test_missing_admitted_participant_cannot_inherit_a_current_link(
    db: AsyncSession, delegation: Delegation
) -> None:
    conversation, parent, researcher, _writer = await _suspend(db, delegation)
    await db.execute(
        delete(ConversationRunAgent).where(
            ConversationRunAgent.run_id == parent.id,
            ConversationRunAgent.agent_id_snapshot == delegation.child_agent_id,
        )
    )
    await db.commit()
    with pytest.raises(AppError) as denied:
        await require_pending_child_authority(
            db,
            conversation=conversation,
            context=delegation.context,
            parent_run=parent,
            pending=_pending(_slot("child-review", researcher)),
        )
    assert denied.value.status in {404, 409}


@pytest.mark.parametrize("read_only", [False, True], ids=["dispatch", "snapshot"])
async def test_child_authority_is_checked_again_after_admission(
    db: AsyncSession, child_case: ChildCase, monkeypatch: pytest.MonkeyPatch, read_only: bool
) -> None:
    graph = child_case.graph
    conversation = await db.get(Conversation, graph.conversation_ids[0])
    parent = await db.get(ConversationRun, child_case.run_id)
    assert conversation and parent
    task_id = await begin_child_task(
        db,
        context=graph.context,
        conversation_id=conversation.id,
        run_id=parent.id,
        tool_call_id="delegate",
        child_agent_id=child_case.child_id,
        role="Researcher",
        checkpoint_ns="tools:delegate",
    )
    assert task_id
    task = await db.get(ConversationChildTask, task_id)
    assert task
    task.status = "awaiting_approval"
    parent.status, parent.is_active, parent.interrupt_id = "interrupted", False, "review"
    await db.commit()
    await _share_child(db, child_case)
    caller = f"user:{graph.context.user_id}"
    root_grant = (caller, "can_run", f"agent:{graph.agent_id}")
    child_grant = (f"agent:{graph.agent_id}", "can_run", f"agent:{child_case.child_id}")
    client = RecordingClient(allowed=frozenset({root_grant, child_grant}))
    monkeypatch.setattr(child_task_authorization, "current_authz", lambda: service(client))
    checkpoint = _pending(_slot("root-review", []), _slot("review", ["tools:delegate", "model:a"]))

    async def authorize() -> None:
        guard = read_pending_child_authority if read_only else require_pending_child_authority
        await guard(
            db,
            conversation=conversation,
            context=graph.context,
            parent_run=parent,
            pending=checkpoint,
        )

    await authorize()
    await db.commit()
    client.allowed = frozenset({root_grant})
    with pytest.raises(AppError) as denied:
        await authorize()
    assert denied.value.status in {404, 409}
    assert task.status == "awaiting_approval"


async def test_root_approval_can_continue_without_an_invoked_child(
    db: AsyncSession, child_case: ChildCase
) -> None:
    graph = child_case.graph
    conversation = await db.get(Conversation, graph.conversation_ids[0])
    parent = await db.get(ConversationRun, child_case.run_id)
    assert conversation and parent
    parent.status, parent.is_active, parent.interrupt_id = "interrupted", False, "root-review"
    await db.execute(
        delete(AgentSubAgentLink).where(AgentSubAgentLink.sub_agent_id == child_case.child_id)
    )
    await db.commit()
    await require_pending_child_authority(
        db,
        conversation=conversation,
        context=graph.context,
        parent_run=parent,
        pending=_pending(_slot("root-review", [])),
    )


async def test_cancelled_child_cannot_be_resumed_from_its_previous_namespace(
    db: AsyncSession, delegation: Delegation
) -> None:
    conversation, parent, researcher, writer = await _suspend(db, delegation)
    target = await db.get(ConversationChildTask, delegation.child_id)
    assert target
    target.status = "cancelled"
    await db.commit()
    with pytest.raises(AppError) as denied:
        await require_pending_child_authority(
            db,
            conversation=conversation,
            context=delegation.context,
            parent_run=parent,
            pending=_pending(_slot("child-review", researcher), _slot("writer", writer)),
        )
    assert denied.value.status in {404, 409}


@pytest.mark.parametrize("prepared", [False, True], ids=["ordinary", "prepared"])
async def test_missing_journal_keeps_native_child_cancellation_binding(
    child_case: ChildCase,
    scoped_session: SessionScope,
    monkeypatch: pytest.MonkeyPatch,
    prepared: bool,
) -> None:
    case = child_case
    ctx, conversation_id = case.graph.context, case.graph.conversation_ids[0]
    saver = InMemorySaver()
    slow = SlowResearch()
    compiled = child_graph(
        case,
        ResearchModel(responses=[]),
        slow.tool(),
        saver,
        prepared,
        root_model=parallel_root(make_agent_runtime_name(case.child_id), ("alpha",)),
        require_approval=True,
    )
    config = run_configurable(str(conversation_id), case.run_id, ctx.tenant_id)
    state = await compiled.ainvoke(
        {"messages": [("user", "Research alpha.")]}, {"configurable": config}
    )
    [interrupt] = state["__interrupt__"]
    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", lambda: saver)
    async with scoped_session(ctx) as db:
        agent_run = await db.get(ConversationRun, case.run_id)
        conversation = await db.get(Conversation, conversation_id)
        assert conversation and agent_run
        agent_run.status, agent_run.is_active, agent_run.interrupt_id = (
            "interrupted",
            False,
            interrupt.id,
        )
        await db.commit()
        child = await db.scalar(
            select(ConversationChildTask).where(ConversationChildTask.current_run_id == case.run_id)
        )
        assert child and child.status == "awaiting_approval"
        pending = await read_pending_checkpoint(
            db, conversation=conversation, context=ctx, parent_run=agent_run
        )
        assert pending and [slot["ns"] for slot in pending.interrupts] == [[]]
        assert (
            pending.root_task_ids[interrupt.id]
            == child.checkpoint_ns.split("|", 1)[0].split(":", 1)[1]
        )
        await require_pending_child_authority(
            db, conversation=conversation, context=ctx, parent_run=agent_run, pending=pending
        )
        assert pending.interrupts[0]["ns"] == child.checkpoint_ns.split("|")
        # Simulate a fresh read after cancellation without a retained journal.
        pending.interrupts[0]["ns"] = []
        await db.commit()
        await child_tasks_api.cancel_child(db, conversation_id, child.id, ctx)
        with pytest.raises(AppError) as denied:
            await require_pending_child_authority(
                db, conversation=conversation, context=ctx, parent_run=agent_run, pending=pending
            )
        assert denied.value.status in {404, 409}
    assert not slow.started["alpha"].is_set()


async def test_reading_mixed_pending_children_acquires_no_locks_and_never_flushes(
    db: AsyncSession, delegation: Delegation
) -> None:
    conversation, parent, researcher, writer = await _suspend(db, delegation)
    pending = _pending(_slot("root", []), _slot("research", researcher), _slot("write", writer))
    # A dirty object exposes accidental autoflush even if the read path itself inserts nothing.
    conversation.title = "Not yet saved"
    with queue_resource_sql(db) as statements:
        legacy = await read_pending_child_authority(
            db,
            conversation=conversation,
            context=delegation.context,
            parent_run=parent,
            pending=pending,
        )
    assert legacy == frozenset()
    assert statements and all(
        sql.lstrip().upper().startswith(("SELECT", "WITH")) for sql in statements
    )
    assert conversation in db.dirty
    transaction = current_state(db)
    assert transaction and transaction.lock_count == 0
    assert live_proof(db) is None
