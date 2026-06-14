from __future__ import annotations

import uuid
from dataclasses import dataclass

import anyio
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.error_codes import conversation_not_found, resume_not_found
from app.models.conversation_run import ConversationRun
from app.services import chat_service, conversation_run_service
from app.services.conversation_audit_service import record_conversation_run_audit
from app.services.conversation_run_worker import get_run_task_registry

_RUN_CANCEL_WAIT_TIMEOUT_SECONDS = 30.0
_RUN_CANCEL_WAIT_INTERVAL_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class ConversationRunCancelResult:
    run: ConversationRun


async def cancel_owned_conversation_run(
    *,
    db: AsyncSession,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
) -> ConversationRunCancelResult:
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if conv is None:
        raise conversation_not_found()

    run = await conversation_run_service.get_run_for_user(
        db,
        conversation_id=conversation_id,
        run_id=run_id,
        user_id=user.id,
        for_update=True,
    )
    if run is None:
        raise resume_not_found()

    previous_status = run.status
    run = await conversation_run_service.request_cancel_run(db, run)
    cancel_requested = previous_status in {"queued", "running"} and run.status == "canceling"
    if cancel_requested:
        await record_conversation_run_audit(
            db,
            action="conversation.run_cancel_request",
            run=run,
            user=user,
            request=request,
            status="canceling",
        )
    await db.commit()

    if cancel_requested:
        registry = get_run_task_registry()
        local_cancel_sent = registry.request_cancel(run_id, reason="user")
        if not local_cancel_sent:
            await db.refresh(run, with_for_update=True)
            if run.status == "canceling":
                await conversation_run_service.transition_run(db, run, "canceled")
                await conversation_run_service.finalize_run_outputs_for_status(
                    db,
                    run,
                    "canceled",
                    append_terminal_event=True,
                )
                await record_conversation_run_audit(
                    db,
                    action="conversation.run_canceled",
                    run=run,
                    user=user,
                    request=request,
                    status="canceled",
                )
                await db.commit()

    await db.refresh(run)
    return ConversationRunCancelResult(run=run)


async def wait_for_run_terminal(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    timeout_seconds: float = _RUN_CANCEL_WAIT_TIMEOUT_SECONDS,
) -> ConversationRun | None:
    deadline = anyio.current_time() + timeout_seconds
    run: ConversationRun | None = None
    while anyio.current_time() < deadline:
        run = await db.get(ConversationRun, run_id)
        if run is None:
            return None
        await db.refresh(run)
        if not run.is_active:
            return run
        await anyio.sleep(_RUN_CANCEL_WAIT_INTERVAL_SECONDS)
    return run or await db.get(ConversationRun, run_id)
