from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_agent_checks import queue_dispatch_denial
from app.authz.conversation_input_scope import owned_input_predicate
from app.authz.conversation_run_scope import owned_run_predicate
from app.authz.execution_lock_cursor import LockClass
from app.authz.org_context import OrgContext
from app.authz.organization_execution_gateway import lock_query
from app.exceptions import AppError
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.conversation_run_input import ConversationRunInput
from app.services import conversation_run_service
from app.services.ag_ui_queue_branch import (
    DEFAULT_BRANCH_AT_CLAIM,
    input_projection_version,
    pin_selected_queue_head,
)
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from app.services.chat.image_description_admission import ADMISSION_KEY
from app.services.conversation_run_queue_blocked import pause_blocked_queue
from app.services.conversation_run_queue_mutations import (
    lock_owned_conversation,
    queue_input_not_found,
    require_queue_executable,
)
from app.services.conversation_run_steer_branch import pin_steer_continuation


@dataclass(frozen=True, slots=True)
class ClaimedConversationInput:
    input: ConversationRunInput
    run: ConversationRun


async def claim_next_input(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    context: OrgContext,
) -> ClaimedConversationInput | None:
    """Bind the next eligible input to a run under the conversation row lock."""
    from app.services.conversation_goal_worker import stop_reservations_on_denial

    try:
        agent_id = await require_queue_executable(db, conversation_id, context)
    except (HTTPException, AppError) as exc:
        if await stop_reservations_on_denial(db, conversation_id, context, exc):
            await db.commit()
        raise
    denial = await queue_dispatch_denial(context, agent_id)
    if denial is not None:  # the input stays pending; a definite denial pauses (F-08)
        stopped = await stop_reservations_on_denial(db, conversation_id, context, denial)
        paused = await pause_blocked_queue(db, conversation_id, agent_id, context, denial)
        if stopped or paused:
            await db.commit()
        return None
    conversation = await lock_owned_conversation(
        db,
        conversation_id=conversation_id,
        context=context,
    )
    if conversation.queue_paused:
        return None
    active = await conversation_run_service.get_active_run(
        db,
        conversation_id=conversation_id,
        context=context,
    )
    if active is not None:
        return None
    unacknowledged_predecessor = await db.scalar(
        select(ConversationRun.id).where(
            ConversationRun.conversation_id == conversation_id,
            owned_run_predicate(context),
            ConversationRun.cancel_requested_at.is_not(None),
            ConversationRun.cancellation_acknowledged_at.is_(None),
        )
    )
    if unacknowledged_predecessor is not None:
        conversation.queue_paused = True
        conversation.queue_paused_at = conversation.queue_paused_at or utc_now_naive()
        await db.flush()
        return None

    queued = await lock_query(
        db,
        LockClass.INPUT,
        select(ConversationRunInput)
        .where(
            ConversationRunInput.conversation_id == conversation_id,
            owned_input_predicate(context),
            ConversationRunInput.status == "pending",
        )
        .order_by(
            (ConversationRunInput.source == "goal_followup").asc(),
            ConversationRunInput.priority.desc(),
            ConversationRunInput.position,
            ConversationRunInput.created_at,
        )
        .limit(1)
        .with_for_update(),
    )
    if queued is None:
        return None
    if queued.agent_id is None or queued.agent_id != agent_id:
        raise queue_input_not_found()
    interrupted = await conversation_run_service.get_latest_interrupted_run(
        db,
        conversation_id=conversation_id,
        context=context,
    )
    if interrupted is not None:
        return None

    if not await pin_steer_continuation(db, conversation, queued, context):
        return None
    version = await db.scalar(input_projection_version(context, conversation_id, queued.id))
    if version is not None and not await pin_selected_queue_head(db, conversation, queued, context):
        return None

    automatic = queued.source == "goal_followup"
    if automatic:
        from app.services.conversation_goal_worker import allow_automatic_start

        if not await allow_automatic_start(
            db, conversation=conversation, input_id=queued.id, context=context
        ):
            queued.status = "canceled"
            queued.revision += 1
            await db.commit()
            return None

    request = OwnedExecutionRequest(
        conversation_id=conversation.id,
        root_agent_id=agent_id,
        context=context,
        source="chat",
        input_preview=None,
        run_metadata={
            **(
                {DEFAULT_BRANCH_AT_CLAIM: True}
                if version is not None
                and queued.source == "chat"
                and conversation.active_branch_checkpoint_id is None
                and queued.checkpoint_id is None
                else {}
            ),
            **(
                {ADMISSION_KEY: queued.image_description_admission}
                if queued.image_description_admission
                else {}
            ),
            **({"ag_ui_projection_version": version} if version is not None else {}),
            "queue_input_id": str(queued.id),
            "client_request_id": queued.client_request_id,
            "checkpoint_id": queued.checkpoint_id,
        },
    )
    try:
        accepted = await accept_owned_execution(db, request)
    except AppError as exc:
        if not automatic or exc.status != 429:
            raise
        from app.services.conversation_goal_service import block_goal_followup

        # Quota checks precede the insertion of a run and participant rows.
        await block_goal_followup(
            db, conversation=conversation, input_id=queued.id, reason="usage_limit"
        )
        queued.status = "canceled"
        queued.revision += 1
        await db.commit()
        return None
    run = await db.get(ConversationRun, accepted.run_id)
    if run is None:
        raise RuntimeError("accepted conversation run was not persisted")
    if automatic:
        from app.services.conversation_goal_worker import INPUT_KEY

        run.metadata_json = {**(run.metadata_json or {}), INPUT_KEY: str(queued.id)}
    queued.run_id = run.id
    queued.status = "claimed"
    queued.claimed_at = utc_now_naive()
    queued.revision += 1
    await db.flush()
    return ClaimedConversationInput(input=queued, run=run)
