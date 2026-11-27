"""A parent terminal transition fences children and settles each receipt in the same transaction."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import delete, event, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, load_only, noload

from app.child_tasks.persistence import begin_child_task, settle_parent_children
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation_child_task import ConversationChildInstruction, ConversationChildTask
from app.models.conversation_run import ConversationRun
from app.services.conversation_run_creation import RunStatus
from app.services.conversation_run_state import transition_run
from app.workflows.pins import agent_fingerprints
from tests.authz.execution_gateway_testkit import seed_graph
from tests.authz.test_child_task_authorization import ChildCase, child_case  # noqa: F401


@pytest.mark.parametrize(
    ("transition", "child_status", "receipt_states"),
    [
        ("completed", "cancelled", ["cancelled", "review_required", "applied"]),
        ("canceling", "cancelled", ["cancelled", "review_required", "applied"]),
        ("failed", "failed", ["cancelled", "review_required", "applied"]),
        ("stale", "failed", ["cancelled", "review_required", "applied"]),
        ("interrupted", "awaiting_approval", ["pending", "delivering", "applied"]),
    ],
)
async def test_parent_transition_is_atomic_and_preserves_actual_receipt_state(
    db: AsyncSession,
    child_case: ChildCase,  # noqa: F811 - pytest registers the imported fixture
    transition: RunStatus,
    child_status: str,
    receipt_states: list[str],
) -> None:
    task_id = await begin_child_task(
        db,
        context=child_case.graph.context,
        conversation_id=child_case.graph.conversation_ids[0],
        run_id=child_case.run_id,
        tool_call_id="call-research",
        child_agent_id=child_case.child_id,
        role="Researcher",
        checkpoint_ns="task:research",
    )
    assert task_id is not None
    await db.commit()
    task = await db.get(ConversationChildTask, task_id)
    assert task is not None
    task.status = "awaiting_approval"
    for sequence, status in enumerate(("pending", "delivering", "applied"), start=1):
        db.add(
            ConversationChildInstruction(
                child_id=task.id,
                client_request_id=str(uuid4()),
                sequence=sequence,
                text="Narrow the result",
                status=status,
            )
        )
    await db.commit()

    parent = await db.get(ConversationRun, child_case.run_id, with_for_update=True)
    assert parent is not None
    await transition_run(db, parent, transition)
    await db.commit()
    await db.refresh(task)
    assert task.status == child_status
    assert (
        list(
            await db.scalars(
                select(ConversationChildInstruction.status)
                .where(ConversationChildInstruction.child_id == task_id)
                .order_by(ConversationChildInstruction.sequence)
            )
        )
        == receipt_states
    )


async def test_late_worker_cannot_complete_or_record_checkpoint_after_child_cancel(
    db: AsyncSession,
    child_case: ChildCase,  # noqa: F811
) -> None:
    from app.child_tasks.errors import ChildTaskStopped
    from app.child_tasks.persistence import record_child_checkpoint, set_child_task_status

    context = child_case.graph.context
    run_id = child_case.run_id
    child_id = await begin_child_task(
        db,
        context=context,
        conversation_id=child_case.graph.conversation_ids[0],
        run_id=run_id,
        tool_call_id="call-cancel",
        child_agent_id=child_case.child_id,
        role="Researcher",
        checkpoint_ns="task:cancel",
    )
    assert child_id is not None
    await db.commit()
    await set_child_task_status(
        db, context=context, child_id=child_id, run_id=run_id, status="cancelled"
    )
    await db.commit()
    with pytest.raises(ChildTaskStopped, match="cancelled"):
        await set_child_task_status(
            db,
            context=context,
            child_id=child_id,
            run_id=run_id,
            status="completed",
            result="late worker",
        )
    await db.rollback()
    with pytest.raises(ChildTaskStopped, match="cancelled"):
        await record_child_checkpoint(
            db, context=context, child_id=child_id, run_id=run_id, checkpoint_id="late-checkpoint"
        )
    await db.rollback()
    saved = await db.get(ConversationChildTask, child_id)
    assert saved and saved.status == "cancelled" and saved.result is None and not saved.checkpointed


@pytest.mark.parametrize("foreign_parent", [False, True], ids=["same-conversation", "other-tenant"])
async def test_resume_settles_only_its_own_ancestor_with_a_projected_run(
    db: AsyncSession,
    child_case: ChildCase,  # noqa: F811
    foreign_parent: bool,
) -> None:
    graph = child_case.graph
    child_id = await begin_child_task(
        db,
        context=graph.context,
        conversation_id=graph.conversation_ids[0],
        run_id=child_case.run_id,
        tool_call_id="call-before-pause",
        child_agent_id=child_case.child_id,
        role="Researcher",
        checkpoint_ns="task:paused",
    )
    assert child_id is not None
    parent = await db.get(ConversationRun, child_case.run_id, with_for_update=True)
    assert parent is not None
    await transition_run(db, parent, "interrupted", interrupt_id="pause")
    await db.commit()

    if foreign_parent:
        # Simulate a malformed parent ID that points across a tenant boundary.
        graph = await seed_graph(db, conversations=1)
    resume = ConversationRun(
        source="resume",
        status="failed",
        parent_run_id=child_case.run_id,
        conversation_id=graph.conversation_ids[0],
        agent_id=graph.agent_id,
        user_id=graph.context.user_id,
        org_id=graph.context.org_id,
        tenant_id=graph.context.tenant_id,
        input_preview="private resume input",
    )
    db.add(resume)
    await db.commit()
    run_id = resume.id
    db.expunge_all()

    locked = await db.scalar(
        select(ConversationRun)
        .where(ConversationRun.id == run_id)
        .with_for_update()
        .options(
            load_only(ConversationRun.id, ConversationRun.status, raiseload=True),
            noload(ConversationRun.metrics),
        )
    )
    assert locked is not None
    assert {"source", "parent_run_id", "input_preview"} <= inspect(locked).unloaded
    selections: list[str] = []

    def record_select(state: ORMExecuteState) -> None:
        if state.is_select:
            selections.append(str(state.statement))

    event.listen(db.sync_session, "do_orm_execute", record_select)
    try:
        await settle_parent_children(db, locked)
    finally:
        event.remove(db.sync_session, "do_orm_execute", record_select)
    assert selections
    for private_column in (
        "conversation_runs.input_preview",
        "conversation_runs.metadata_json",
        "conversation_child_tasks.brief",
        "conversation_child_tasks.result",
        "conversation_child_instructions.text",
    ):
        assert all(private_column not in sql for sql in selections)
    assert {"input_preview", "metadata_json"} <= inspect(locked).unloaded
    await db.commit()

    status = await db.scalar(
        select(ConversationChildTask.status).where(ConversationChildTask.id == child_id)
    )
    assert status == ("running" if foreign_parent else "failed")


async def test_workflow_pins_follow_delegation_config_independently_of_chat_invocations(
    db: AsyncSession,
    child_case: ChildCase,  # noqa: F811
) -> None:
    graph = child_case.graph
    agents = [graph.agent_id, child_case.child_id]
    published = await agent_fingerprints(db, agents)
    child_task_id = await begin_child_task(
        db,
        context=graph.context,
        conversation_id=graph.conversation_ids[0],
        run_id=child_case.run_id,
        tool_call_id="call-research",
        child_agent_id=child_case.child_id,
        role="Researcher",
        checkpoint_ns="task:research",
    )
    assert child_task_id is not None
    await db.commit()
    assert await agent_fingerprints(db, agents) == published

    await db.execute(
        delete(AgentSubAgentLink).where(
            AgentSubAgentLink.parent_agent_id == graph.agent_id,
            AgentSubAgentLink.sub_agent_id == child_case.child_id,
        )
    )
    updated = await agent_fingerprints(db, agents)
    assert updated[graph.agent_id] != published[graph.agent_id]
    assert updated[child_case.child_id] == published[child_case.child_id]
