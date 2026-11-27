"""A parent terminal transition fences children and settles each receipt in the same transaction."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_child_task import ConversationChildInstruction, ConversationChildTask
from app.models.conversation_run import ConversationRun
from app.services.child_tasks_runtime import begin_child_task
from app.services.conversation_run_creation import RunStatus
from app.services.conversation_run_state import transition_run
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
    from app.services.child_task_errors import ChildTaskStopped
    from app.services.child_tasks_runtime import record_child_checkpoint, set_child_task_status

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
