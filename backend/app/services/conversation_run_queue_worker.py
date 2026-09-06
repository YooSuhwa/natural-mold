from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException
from sqlalchemy import select

from app.agent_runtime.executor import execute_agent_stream_langgraph
from app.agent_runtime.runtime_config import AgentConfig
from app.database import async_session as database_session
from app.dependencies import CurrentUser
from app.exceptions import ConflictError
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.user import User
from app.services import conversation_run_queue_service, conversation_run_service
from app.services.conversation_run_cancellation_recovery import (
    finalize_workerless_cancel_before_start,
)
from app.services.conversation_run_worker import (
    RunTaskAlreadyRegisteredError,
    get_run_task_registry,
    start_conversation_run,
)
from app.services.conversation_stream_service import resolve_agent_context

async_session = None
logger = logging.getLogger(__name__)


def _session_factory():
    return async_session or database_session


def _current_user(user: User) -> CurrentUser:
    return CurrentUser(
        id=user.id,
        email=user.email,
        name=user.name,
        display_name=user.display_name,
        avatar_mode=user.avatar_mode,
        avatar_initials=user.avatar_initials,
        avatar_color=user.avatar_color,
        avatar_image_url=user.avatar_image_url,
        is_super_user=user.is_super_user,
    )


async def launch_claimed_input(input_id: uuid.UUID) -> ConversationRun | None:
    """Rehydrate and launch one claimed input without request-scoped state."""
    registry = get_run_task_registry()
    cfg: AgentConfig | None = None
    current_user: CurrentUser | None = None
    error_code: str | None = None
    async with _session_factory()() as db:
        queued = await db.get(ConversationRunInput, input_id)
        if queued is None or queued.run_id is None or queued.status != "claimed":
            return None
        run = await db.get(ConversationRun, queued.run_id)
        user = await db.get(User, queued.user_id)
        if run is None:
            return None
        if user is None or not user.is_active:
            error_code = "queue_user_unavailable"
        else:
            local_task = registry.get(run.id)
            if local_task is not None and not local_task.done():
                return run
            current_user = _current_user(user)
            try:
                cfg = await resolve_agent_context(
                    db,
                    queued.conversation_id,
                    current_user,
                    checkpoint_id=queued.checkpoint_id,
                )
            except (ConflictError, HTTPException, ValueError):
                logger.warning(
                    "queued input rehydration failed input_id=%s",
                    input_id,
                    exc_info=True,
                )
                error_code = "queue_rehydrate_failed"
        attachment_ids = [uuid.UUID(value) for value in queued.attachment_ids]
        input_payload = queued.input_payload
        conversation_id = queued.conversation_id
        run_id = run.id
        source = queued.source

    if error_code is not None:
        await _fail_claimed_input(input_id, error_code=error_code)
        return run
    if cfg is None or current_user is None:
        await _fail_claimed_input(input_id, error_code="queue_rehydrate_failed")
        return run

    local_task = registry.get(run_id)
    if local_task is not None and not local_task.done():
        return run
    try:
        await start_conversation_run(
            run_id=run_id,
            conversation_id=conversation_id,
            cfg=cfg,
            user=current_user,
            input_payload=input_payload,
            moldy_source=source,
            executor_fn=execute_agent_stream_langgraph,
            registry=registry,
            attachment_ids=attachment_ids,
        )
    except RunTaskAlreadyRegisteredError:
        local_task = registry.get(run_id)
        if local_task is None or local_task.done():
            raise
    return run


async def _fail_claimed_input(input_id: uuid.UUID, *, error_code: str) -> None:
    async with _session_factory()() as db:
        queued = await db.get(ConversationRunInput, input_id)
        if queued is None or queued.run_id is None:
            return
        conversation = await db.get(Conversation, queued.conversation_id, with_for_update=True)
        run = await db.get(ConversationRun, queued.run_id, with_for_update=True)
        if conversation is None or run is None or run.status != "queued":
            return
        queued.status = "failed"
        queued.revision += 1
        await conversation_run_service.transition_run(
            db,
            run,
            "failed",
            error_code=error_code,
            error_message="Queued input could not be reconstructed.",
        )
        await db.commit()


async def dispatch_next_for_conversation(
    conversation_id: uuid.UUID,
) -> ConversationRunInput | None:
    """Claim, commit, then launch the next input for one conversation."""
    async with _session_factory()() as db:
        owner_id = await db.scalar(
            select(Agent.user_id)
            .join(Conversation, Conversation.agent_id == Agent.id)
            .where(Conversation.id == conversation_id)
        )
        if owner_id is None:
            return None
        claimed = await conversation_run_queue_service.claim_next_input(
            db,
            conversation_id=conversation_id,
            user_id=owner_id,
        )
        if claimed is None:
            return None
        input_id = claimed.input.id
        await db.commit()
    await launch_claimed_input(input_id)
    return claimed.input


async def recover_conversation_queue() -> int:
    """Recover claim-before-launch gaps and dispatch idle persisted queues."""
    registry = get_run_task_registry()
    await finalize_workerless_cancel_before_start(
        session_factory=_session_factory(),
        protected_run_ids=tuple(registry.active_run_ids()),
    )
    async with _session_factory()() as db:
        claimed_result = await db.execute(
            select(ConversationRunInput)
            .join(ConversationRun, ConversationRun.id == ConversationRunInput.run_id)
            .where(
                ConversationRunInput.status == "claimed",
                ConversationRun.status == "queued",
                ConversationRun.worker_instance_id.is_(None),
            )
        )
        claimed_ids = [
            item.id
            for item in claimed_result.scalars().all()
            if item.run_id not in registry.active_run_ids()
        ]
        pending_result = await db.execute(
            select(ConversationRunInput.conversation_id)
            .join(Conversation, Conversation.id == ConversationRunInput.conversation_id)
            .where(
                ConversationRunInput.status == "pending",
                Conversation.queue_paused.is_(False),
            )
            .distinct()
        )
        pending_conversation_ids = list(pending_result.scalars().all())

    launched = 0
    for input_id in claimed_ids:
        if await launch_claimed_input(input_id) is not None:
            launched += 1
    for conversation_id in pending_conversation_ids:
        if await dispatch_next_for_conversation(conversation_id) is not None:
            launched += 1
    return launched
