"""Persistence at child execution boundaries. The caller owns each short transaction."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.authz.child_task_authorization import (
    authorize_child_target,
    require_same_child_identity,
)
from app.authz.conversation_run_scope import private_run_predicate
from app.authz.execution_transaction_tracker import claimed_locks
from app.authz.org_context import OrgContext
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation_child_task import ConversationChildInstruction, ConversationChildTask
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.services.child_task_errors import ChildTaskStopped

_TERMINAL = frozenset({"cancelled", "failed", "completed"})
_OPEN = ("running", "awaiting_approval")


def _stopped(status: str) -> ChildTaskStopped:
    if status == "awaiting_approval":
        return ChildTaskStopped("awaiting_approval")
    if status == "cancelled":
        return ChildTaskStopped("cancelled")
    return ChildTaskStopped("child_terminal")


def _resume_lineage(run: ConversationRun) -> Select[tuple[UUID]]:
    """Only original parent runs that actually paused this same private conversation."""
    lineage = (
        select(ConversationRun.id, ConversationRun.source, ConversationRun.parent_run_id)
        .where(ConversationRun.id == run.id)
        .cte("child_run_lineage", recursive=True)
    )
    # UNION (distinct) is also safe if a corrupt row contains a cycle.
    lineage = lineage.union(
        select(ConversationRun.id, ConversationRun.source, ConversationRun.parent_run_id)
        .join(lineage, ConversationRun.id == lineage.c.parent_run_id)
        .where(
            lineage.c.source == "resume",
            ConversationRun.status == "interrupted",
            private_run_predicate(OrgContext(run.org_id, run.tenant_id, run.user_id)),
            ConversationRun.conversation_id == run.conversation_id,
            ConversationRun.agent_id == run.agent_id,
        )
    )
    return select(lineage.c.id)


async def has_open_child_tasks(
    db: AsyncSession, *, context: OrgContext, conversation_id: UUID
) -> bool:
    found = await db.scalar(
        select(ConversationChildTask.id)
        .join(ConversationRun, ConversationRun.id == ConversationChildTask.current_run_id)
        .where(
            ConversationRun.conversation_id == conversation_id,
            private_run_predicate(context),
            ConversationChildTask.status.in_(_OPEN),
        )
        .limit(1)
    )
    return found is not None


async def begin_child_task(
    db: AsyncSession,
    *,
    context: OrgContext,
    conversation_id: UUID,
    run_id: UUID,
    tool_call_id: str,
    child_agent_id: UUID,
    role: str,
    checkpoint_ns: str,
    brief: str | None = None,
    allow_create: bool = True,
) -> UUID | None:
    """Bind one exact invocation. Resumed parents can claim their own suspended invocation."""
    if not (
        0 < len(tool_call_id) <= 256 and 0 < len(checkpoint_ns) <= 1024 and 0 < len(role) <= 160
    ):
        raise ChildTaskStopped("revoked")
    participants = await db.execute(
        select(ConversationRunAgent.role, ConversationRunAgent.identity_mode_at_accept)
        .join(ConversationRun, ConversationRunAgent.run_id == ConversationRun.id)
        .where(
            ConversationRun.id == run_id,
            ConversationRun.conversation_id == conversation_id,
            ConversationRun.status == "running",
            ConversationRun.cancel_requested_at.is_(None),
            private_run_predicate(context),
            ConversationRunAgent.agent_id == ConversationRunAgent.agent_id_snapshot,
            (
                (ConversationRunAgent.role == "root")
                & (ConversationRunAgent.agent_id == ConversationRun.agent_id)
            )
            | (
                (ConversationRunAgent.role == "subagent")
                & (ConversationRunAgent.agent_id == child_agent_id)
            ),
        )
    )
    pins = dict(participants.tuples().all())
    if pins.keys() != {"root", "subagent"}:
        raise ChildTaskStopped("revoked")
    legacy = all(mode is None for mode in pins.values())
    if legacy or not allow_create:
        # Original pre-feature invocations continue through their normal handler.
        # A lost pin on an already-bound invocation must never open that path.
        scoped_run = await db.get(ConversationRun, run_id)
        if scoped_run is None:
            raise ChildTaskStopped("revoked")
        exists = await db.scalar(
            select(ConversationChildTask.id)
            .where(
                ConversationChildTask.tool_call_id == tool_call_id,
                ConversationChildTask.current_run_id.in_(_resume_lineage(scoped_run)),
            )
            .limit(1)
        )
        if exists is None:
            return None
        if legacy:
            raise ChildTaskStopped("revoked")
    run = await authorize_child_target(db, context, run_id, child_agent_id)
    if run.conversation_id != conversation_id:
        raise ChildTaskStopped("revoked")

    # A chat's run row serializes registrations, including parallel calls from siblings.
    query = select(ConversationChildTask).where(ConversationChildTask.tool_call_id == tool_call_id)
    with claimed_locks(db):
        child = await db.scalar(
            query.where(ConversationChildTask.current_run_id == run.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if child is None and run.source == "resume" and run.parent_run_id is not None:
            ancestors = list(
                await db.scalars(
                    query.where(ConversationChildTask.current_run_id.in_(_resume_lineage(run)))
                    .order_by(ConversationChildTask.id)
                    .limit(2)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            if len(ancestors) > 1:
                raise ChildTaskStopped("revoked")
            child = ancestors[0] if ancestors else None
    if child is None:
        if not allow_create:
            raise ChildTaskStopped("revoked")
        child = ConversationChildTask(
            run_id=run.id,
            current_run_id=run.id,
            tool_call_id=tool_call_id,
            checkpoint_ns=checkpoint_ns,
            child_agent_id=child_agent_id,
            role=role,
            brief=brief[:300] if brief else None,
        )
        db.add(child)
    else:
        if child.child_agent_id != child_agent_id or child.checkpoint_ns != checkpoint_ns:
            raise ChildTaskStopped("revoked")
        if child.status not in _OPEN:
            raise _stopped(child.status)
        if child.status == "awaiting_approval" and child.current_run_id == run.id:
            raise ChildTaskStopped("awaiting_approval")
        if child.current_run_id != run.id:
            if child.status != "awaiting_approval":
                raise ChildTaskStopped("revoked")
            await require_same_child_identity(
                db,
                previous_run_id=child.run_id,
                current_run_id=run.id,
                child_agent_id=child_agent_id,
            )
            child.current_run_id = run.id
        child.status = "running"
    await db.flush()
    return child.id


async def _locked_child(
    db: AsyncSession,
    context: OrgContext,
    child_id: UUID,
    run_id: UUID,
    *,
    require_active: bool = True,
) -> ConversationChildTask:
    ref = (
        await db.execute(
            select(
                ConversationChildTask.current_run_id, ConversationChildTask.child_agent_id
            ).where(ConversationChildTask.id == child_id)
        )
    ).one_or_none()
    if ref is None or ref.current_run_id != run_id:
        raise ChildTaskStopped("revoked")
    await authorize_child_target(
        db, context, ref.current_run_id, ref.child_agent_id, require_active=require_active
    )
    with claimed_locks(db):
        child = await db.scalar(
            select(ConversationChildTask)
            .where(
                ConversationChildTask.id == child_id,
                ConversationChildTask.current_run_id == ref.current_run_id,
                ConversationChildTask.child_agent_id == ref.child_agent_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    if child is None:
        raise ChildTaskStopped("revoked")
    return child


async def child_cancel_requested(
    db: AsyncSession, *, context: OrgContext, child_id: UUID, run_id: UUID
) -> bool:
    """Poll in-flight cancellation. Dispatch boundaries check fresh authorization."""
    cancelled = await db.scalar(
        select(ConversationChildTask.id)
        .join(ConversationRun, ConversationRun.id == ConversationChildTask.current_run_id)
        .where(
            ConversationChildTask.id == child_id,
            ConversationRun.id == run_id,
            private_run_predicate(context),
            ConversationChildTask.status == "cancelled",
        )
    )
    return cancelled is not None


async def check_child_task(
    db: AsyncSession, *, context: OrgContext, child_id: UUID, run_id: UUID
) -> None:
    child = await _locked_child(db, context, child_id, run_id)
    if child.status != "running":
        raise _stopped(child.status)


async def next_child_instructions(
    db: AsyncSession, *, context: OrgContext, child_id: UUID, run_id: UUID
) -> list[tuple[UUID, str, bool]]:
    child = await _locked_child(db, context, child_id, run_id)
    if child.status != "running":
        raise _stopped(child.status)
    rows = list(
        await db.scalars(
            select(ConversationChildInstruction)
            .where(
                ConversationChildInstruction.child_id == child_id,
                ConversationChildInstruction.status.in_(("pending", "delivering")),
            )
            .order_by(ConversationChildInstruction.sequence)
        )
    )
    claims = [(receipt.id, receipt.text, receipt.status == "pending") for receipt in rows]
    now = utc_now_naive()
    for receipt in rows:
        if receipt.status == "pending":
            receipt.status, receipt.delivery_started_at = "delivering", now
    await db.flush()
    return claims


async def record_child_checkpoint(
    db: AsyncSession, *, context: OrgContext, child_id: UUID, run_id: UUID, checkpoint_id: str
) -> None:
    child = await _locked_child(db, context, child_id, run_id)
    if child.status != "running":
        raise _stopped(child.status)
    if not 0 < len(checkpoint_id) <= 128:
        raise ValueError("Invalid child checkpoint ID")
    child.checkpoint_id = checkpoint_id
    await db.flush()


async def confirm_child_instructions(
    db: AsyncSession,
    *,
    context: OrgContext,
    child_id: UUID,
    run_id: UUID,
    ids: set[UUID],
    checkpoint_id: str | None = None,
) -> None:
    child = await _locked_child(db, context, child_id, run_id)
    if child.status != "running":
        raise _stopped(child.status)
    if checkpoint_id is not None:
        if not 0 < len(checkpoint_id) <= 128:
            raise ValueError("Invalid child checkpoint ID")
        child.checkpoint_id = checkpoint_id
    if ids:
        await db.execute(
            update(ConversationChildInstruction)
            .where(
                ConversationChildInstruction.id.in_(ids),
                ConversationChildInstruction.child_id == child_id,
                ConversationChildInstruction.status == "delivering",
            )
            .values(status="applied", applied_at=utc_now_naive())
        )
    await db.flush()


async def settle_child_status_locked(
    db: AsyncSession, child: ConversationChildTask, *, status: str, result: str | None = None
) -> None:
    """Settle only open tasks. A late worker cannot overwrite a recorded cancellation."""
    if status not in (*_OPEN, *_TERMINAL):
        raise ValueError("Unknown child status")
    if child.status in _TERMINAL:
        return
    child.status = status
    if status == "completed" and result is not None:
        child.result = result[:16000]
    if status in _TERMINAL:
        for previous, target in (("pending", "cancelled"), ("delivering", "review_required")):
            await db.execute(
                update(ConversationChildInstruction)
                .where(
                    ConversationChildInstruction.child_id == child.id,
                    ConversationChildInstruction.status == previous,
                )
                .values(status=target)
            )
    await db.flush()


async def set_child_task_status(
    db: AsyncSession,
    *,
    context: OrgContext,
    child_id: UUID,
    run_id: UUID,
    status: str,
    result: str | None = None,
) -> None:
    child = await _locked_child(db, context, child_id, run_id, require_active=status != "cancelled")
    if child.status in _TERMINAL and child.status != status:
        raise _stopped(child.status)
    await settle_child_status_locked(db, child, status=status, result=result)


async def settle_parent_children(db: AsyncSession, run: ConversationRun) -> None:
    """Fence remaining parent-bound tasks within the run's terminal transaction."""
    status = {
        "canceling": "cancelled",
        "canceled": "cancelled",
        "failed": "failed",
        "stale": "failed",
        "completed": "cancelled",
    }.get(run.status)
    if status is None:
        return
    # Parent run lock is already held. Every writer locks parent first.
    with claimed_locks(db):
        rows = list(
            await db.scalars(
                select(ConversationChildTask)
                .where(
                    (
                        ConversationChildTask.current_run_id.in_(_resume_lineage(run))
                        if run.source == "resume" and run.parent_run_id is not None
                        else ConversationChildTask.current_run_id == run.id
                    ),
                    ConversationChildTask.status.in_(_OPEN),
                )
                .order_by(ConversationChildTask.id)
                .with_for_update()
            )
        )
    for child in rows:
        await settle_child_status_locked(db, child, status=status)
