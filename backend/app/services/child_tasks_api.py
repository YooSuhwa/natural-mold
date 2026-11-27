"""Private projections and serialized user commands for in-conversation child tasks."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.authz.conversation_run_scope import private_run_predicate
from app.authz.conversation_scope import require_private_conversation
from app.authz.execution_transaction_tracker import claimed_locks
from app.authz.org_context import OrgContext
from app.authz.run_decisions import open_request_memo
from app.config import settings
from app.exceptions import AppError, ConflictError, NotFoundError
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_child_task import ConversationChildInstruction, ConversationChildTask
from app.models.conversation_run import ConversationRun
from app.schemas.conversation_child_task import (
    ChildInstructionCreate,
    ChildTaskPage,
    ChildTaskResponse,
)
from app.services.child_tasks_runtime import (
    ChildTaskStopped,
    authorize_child_target,
    settle_child_status_locked,
)
from app.services.conversation_run_service import readable_conversation_run_ids

_CONVERSATIONAL = {"running", "awaiting_approval"}


def _not_found() -> NotFoundError:
    return NotFoundError("CHILD_TASK_NOT_FOUND", "위임한 작업을 찾을 수 없습니다.")


async def _authorize_target(
    db: AsyncSession,
    context: OrgContext,
    child: ConversationChildTask,
) -> ConversationRun:
    try:
        return await authorize_child_target(
            db, context, child.current_run_id, child.child_agent_id, require_active=False
        )
    except ChildTaskStopped as exc:
        if exc.reason == "revoked":
            raise _not_found() from None
        raise ConflictError("CHILD_TASK_STOPPED", "위임한 작업이 종료되었습니다.") from None
    except AppError as exc:
        if exc.status in {403, 404}:
            raise _not_found() from None
        raise


async def _view(db: AsyncSession, child: ConversationChildTask) -> ChildTaskResponse:
    await db.refresh(child, ["instructions"])
    return ChildTaskResponse.model_validate(child)


async def _in_conversation(
    db: AsyncSession, conversation: Conversation, context: OrgContext
) -> Select[tuple[ConversationChildTask]]:
    current = aliased(ConversationRun)
    stmt = (
        select(ConversationChildTask)
        .join(ConversationRun, ConversationRun.id == ConversationChildTask.run_id)
        .join(current, current.id == ConversationChildTask.current_run_id)
        .where(
            ConversationRun.conversation_id == conversation.id,
            current.conversation_id == ConversationRun.conversation_id,
            private_run_predicate(context),
        )
        .execution_options(populate_existing=True)
    )
    readable = await readable_conversation_run_ids(db, conversation, context)
    if readable is not None:
        stmt = stmt.where(
            ConversationChildTask.run_id.in_(readable),
            ConversationChildTask.current_run_id.in_(readable),
        )
    return stmt


async def _allow_legacy_details(
    db: AsyncSession, conversation_id: UUID, context: OrgContext
) -> bool:
    # The immutable root pin also covers runs that never launched a child. Filtering
    # on child rows here would expose the existence of a currently inaccessible task.
    accepted_root = (
        select(ConversationRunAgent.id)
        .join(ConversationRun, ConversationRun.id == ConversationRunAgent.run_id)
        .where(
            ConversationRun.conversation_id == conversation_id,
            ConversationRun.run_source == "CHAT",
            private_run_predicate(context),
            ConversationRunAgent.role == "root",
            ConversationRunAgent.identity_mode_at_accept.is_not(None),
        )
    )
    return not await db.scalar(select(accepted_root.exists()))


async def _candidate_ids(
    db: AsyncSession, scoped: Select[tuple[ConversationChildTask]], batch_size: int
) -> AsyncIterator[UUID]:
    ordered = scoped.with_only_columns(ConversationChildTask.id).order_by(
        ConversationChildTask.status.in_(_CONVERSATIONAL).desc(),
        ConversationChildTask.created_at.desc(),
        ConversationChildTask.id,
    )
    raw_offset = 0
    while True:
        ids = (await db.scalars(ordered.offset(raw_offset).limit(batch_size))).all()
        # Each target's authorization must acquire its own proof and locks.
        await db.rollback()
        for child_id in ids:
            yield child_id
        if len(ids) < batch_size:
            return
        raw_offset += len(ids)


async def list_child_tasks(
    db: AsyncSession,
    conversation_id: UUID,
    context: OrgContext,
    *,
    limit: int = 50,
    offset: int = 0,
) -> ChildTaskPage:
    conversation = await require_private_conversation(db, conversation_id, context)
    enabled = settings.child_tasks_enabled
    allow_legacy = (
        not enabled
        and conversation.source != INDEPENDENT_JOB_SOURCE
        and await _allow_legacy_details(db, conversation_id, context)
    )
    scoped = await _in_conversation(db, conversation, context)
    result: list[ChildTaskResponse] = []
    visible_count = 0
    next_offset = None
    # The public offset counts authorized children only, including the lookahead.
    async for child_id in _candidate_ids(db, scoped, max(50, limit + 1)):
        open_request_memo()
        try:
            child, _ = await _load(db, conversation_id, child_id, context)
            if len(result) == limit:
                next_offset = offset + limit
                break
            if visible_count >= offset:
                result.append(await _view(db, child))
            visible_count += 1
        except NotFoundError:
            pass
        finally:
            await db.rollback()
    return ChildTaskPage(
        items=result,
        next_offset=next_offset,
        enabled=enabled,
        allow_legacy_details=allow_legacy,
    )


async def _load(
    db: AsyncSession,
    conversation_id: UUID,
    child_id: UUID,
    context: OrgContext,
    *,
    command: bool = False,
) -> tuple[ConversationChildTask, ConversationRun]:
    conversation = await require_private_conversation(db, conversation_id, context)
    scoped = await _in_conversation(db, conversation, context)
    stmt = scoped.where(ConversationChildTask.id == child_id)
    child = await db.scalar(stmt)
    if child is None:
        raise _not_found()
    current_id = child.current_run_id
    run = await _authorize_target(db, context, child)
    if command and conversation.source == INDEPENDENT_JOB_SOURCE:
        raise ConflictError(
            "CHILD_TASK_CLOSED", "현재 상태에서는 위임한 작업을 변경할 수 없습니다."
        )
    # Recheck the binding under the same authorization, including on reads. A
    # resumed parent can take over a suspended child while its old run is checked.
    with claimed_locks(db):
        child = await db.scalar(stmt.with_for_update(of=ConversationChildTask))
    if child is None:
        raise _not_found()
    if child.current_run_id != current_id:
        raise ConflictError("CHILD_TASK_MOVED", "실행이 재개되었습니다. 다시 시도해 주세요.")
    return child, run


async def _require_open(
    db: AsyncSession,
    context: OrgContext,
    child: ConversationChildTask,
    run: ConversationRun,
) -> None:
    # Approval resume creates a new parent run. Never add a command to the consumed
    # interrupt while the child has not rebound to its successor.
    parent_open = run.is_active and run.status == "running"
    if run.status == "interrupted" and child.status == "awaiting_approval":
        successor = await db.scalar(
            select(ConversationRun.id)
            .where(
                ConversationRun.conversation_id == run.conversation_id,
                ConversationRun.parent_run_id == run.id,
                ConversationRun.source == "resume",
                private_run_predicate(context),
            )
            .limit(1)
        )
        parent_open = successor is None
    if not parent_open or run.cancel_requested_at or child.status not in _CONVERSATIONAL:
        raise ConflictError(
            "CHILD_TASK_CLOSED", "현재 상태에서는 위임한 작업을 변경할 수 없습니다."
        )


async def instruct_child(
    db: AsyncSession,
    conversation_id: UUID,
    child_id: UUID,
    context: OrgContext,
    payload: ChildInstructionCreate,
) -> ChildTaskResponse:
    child, run = await _load(db, conversation_id, child_id, context, command=True)
    request = await db.scalar(
        select(ConversationChildInstruction).where(
            ConversationChildInstruction.child_id == child.id,
            ConversationChildInstruction.client_request_id == payload.client_request_id,
        )
    )
    if request:
        if request.text != payload.text:
            raise ConflictError(
                "CHILD_INSTRUCTION_CONFLICT", "같은 요청 ID에 다른 지시가 있습니다."
            )
    else:
        if not settings.child_tasks_enabled:
            raise ConflictError("CHILD_TASKS_DISABLED", "현재 새 지시를 접수할 수 없습니다.")
        await _require_open(db, context, child, run)
        last = await db.scalar(
            select(func.max(ConversationChildInstruction.sequence)).where(
                ConversationChildInstruction.child_id == child.id
            )
        )
        db.add(
            ConversationChildInstruction(
                child_id=child.id,
                client_request_id=payload.client_request_id,
                sequence=(last or 0) + 1,
                text=payload.text,
                status="pending",
            )
        )
        await db.flush()
    response = await _view(db, child)
    await db.commit()
    return response


async def cancel_child(
    db: AsyncSession,
    conversation_id: UUID,
    child_id: UUID,
    context: OrgContext,
) -> ChildTaskResponse:
    child, run = await _load(db, conversation_id, child_id, context, command=True)
    if child.status != "cancelled":
        await _require_open(db, context, child, run)
        await settle_child_status_locked(db, child, status="cancelled")
        await db.flush()
        await db.refresh(child)
    response = await _view(db, child)
    await db.commit()
    return response


async def wait_for_child(
    db: AsyncSession,
    conversation_id: UUID,
    child_id: UUID,
    context: OrgContext,
    timeout_seconds: float,
) -> ChildTaskResponse:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        open_request_memo()
        child, _ = await _load(db, conversation_id, child_id, context)
        response = await _view(db, child)
        await db.rollback()
        remaining = deadline - loop.time()
        if response.status != "running" or remaining <= 0:
            return response
        await asyncio.sleep(min(0.5, remaining))
