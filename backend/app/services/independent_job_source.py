"""Resolve a target selected from a delegated task card after parent approval resumes."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.child_task_authorization import verify_child_task_identity
from app.authz.org_context import OrgContext
from app.child_tasks.errors import ChildTaskStopped
from app.error_codes import resume_not_found
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation_child_task import ConversationChildTask
from app.models.conversation_run import ConversationRun


async def require_source_task(
    db: AsyncSession, task_id: UUID, origin: ConversationRun, target_id: UUID, context: OrgContext
) -> None:
    task = await db.scalar(
        select(ConversationChildTask).where(
            ConversationChildTask.id == task_id,
            ConversationChildTask.run_id == origin.id,
            ConversationChildTask.child_agent_id == target_id,
            ConversationChildTask.status != "cancelled",
        )
    )
    if task is None:
        raise resume_not_found()
    try:
        await verify_child_task_identity(
            db,
            context=context,
            original_run_id=origin.id,
            current_run_id=task.current_run_id,
            child_agent_id=target_id,
        )
    except ChildTaskStopped:
        raise resume_not_found() from None
    if task.current_run_id == origin.id:
        return

    # The source card keeps its original tool identity while approvals move its
    # execution to a new run. Check every link back to the original parent.
    run = ConversationRun
    columns = (run.id, run.source, run.parent_run_id)
    scope = (
        run.conversation_id == origin.conversation_id,
        run.user_id == origin.user_id,
        run.org_id == origin.org_id,
        run.tenant_id == origin.tenant_id,
        run.agent_id == origin.agent_id,
        run.run_source == "CHAT",
    )
    chain = (
        select(*columns)
        .where(
            *scope,
            run.id == task.current_run_id,
            run.status.in_(("running", "queued", "interrupted", "completed")),
        )
        .cte(recursive=True)
    )
    chain = chain.union(
        select(*columns).where(
            *scope,
            run.id == chain.c.parent_run_id,
            chain.c.source == "resume",
            run.status == "interrupted",
        )
    )
    origin_found = await db.scalar(select(chain.c.id).where(chain.c.id == origin.id))
    current_participant = await db.scalar(
        select(ConversationRunAgent).where(
            ConversationRunAgent.run_id == task.current_run_id,
            ConversationRunAgent.agent_id_snapshot == target_id,
            ConversationRunAgent.agent_id == target_id,
            ConversationRunAgent.role == "subagent",
        )
    )
    if (
        origin_found is None
        or current_participant is None
        or current_participant.path != [str(origin.agent_id), str(target_id)]
    ):
        raise resume_not_found()
