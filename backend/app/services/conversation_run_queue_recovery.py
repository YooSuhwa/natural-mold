from __future__ import annotations

import logging

from sqlalchemy import select

from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services.conversation_run_cancellation_recovery import (
    finalize_workerless_cancel_before_start,
)
from app.services.conversation_run_queue_launch import facade_attr

logger = logging.getLogger("app.services.conversation_run_queue_worker")


async def recover_conversation_queue() -> int:
    """Recover claim-before-launch gaps and dispatch idle persisted queues."""
    registry = facade_attr("get_run_task_registry")()
    await finalize_workerless_cancel_before_start(
        session_factory=facade_attr("_session_factory")(),
        protected_run_ids=tuple(registry.active_run_ids()),
    )
    async with facade_attr("_session_factory")()() as db:
        claimed_result = await db.execute(
            select(ConversationRunInput.id, ConversationRunInput.run_id)
            .join(ConversationRun, ConversationRun.id == ConversationRunInput.run_id)
            .where(
                ConversationRunInput.status == "claimed",
                ConversationRun.status == "queued",
                ConversationRun.worker_instance_id.is_(None),
            )
        )
        claimed_ids = [
            item.id for item in claimed_result.all() if item.run_id not in registry.active_run_ids()
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
        if await facade_attr("launch_claimed_input")(input_id) is not None:
            launched += 1
    for conversation_id in pending_conversation_ids:
        try:
            if await facade_attr("dispatch_next_for_conversation")(conversation_id) is not None:
                launched += 1
        except AppError:
            logger.info(
                "queue recovery skipped non-executable conversation conversation_id=%s",
                conversation_id,
            )
    return launched
