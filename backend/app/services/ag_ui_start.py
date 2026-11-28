"""Accept a new AG-UI user turn using the conversation's existing durable worker."""

from __future__ import annotations

import hashlib
from uuid import UUID

from ag_ui.core import RunAgentInput
from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.error_codes import conversation_not_found
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run_input import JsonValue
from app.schemas.conversation_graph_input import is_omitted_user_message
from app.services import chat_service, conversation_run_queue_service, conversation_run_service
from app.services.ag_ui_commands import AgUiRunBinding, CommandAdmission, bind_command
from app.services.ag_ui_start_effects import apply_start_effects, authorize_start_attachments
from app.services.ag_ui_start_input import OMITTED_MEDIA_SUFFIX, parse_start_input
from app.services.chat_resource_context_integration import (
    QueuedResourceContextInput,
    freeze_queued_resource_context_payload,
)
from app.services.chat_resource_context_sources import ResourceContextScope
from app.services.conversation_agent_context import resolve_agent_context
from app.services.conversation_run_queue_payload import input_preview
from app.services.conversation_run_queue_promotion import interrupt_active_for_input


def _queued_message_identity(payload: dict[str, JsonValue]) -> tuple[UUID, bool]:
    messages = payload.get("messages")
    if not isinstance(messages, list) or len(messages) != 1:
        raise ValueError("Expected one normalized AG-UI user message")
    message = messages[0]
    if not isinstance(message, dict) or message.get("role") != "user":
        raise ValueError("Expected a normalized AG-UI user message")
    message_id = message.get("id")
    if not isinstance(message_id, str):
        raise ValueError("Missing server-generated user message ID")
    return UUID(message_id), is_omitted_user_message(message)


async def accept_start(
    *,
    db: AsyncSession,
    context: OrgContext,
    user: CurrentUser,
    conversation: Conversation,
    request: Request,
    run_input: RunAgentInput,
    admission: CommandAdmission,
) -> AgUiRunBinding:
    """Bind the validated command and work to the caller's open admission transaction."""
    if (
        admission.context != context
        or admission.conversation_id != conversation.id
        or user.id != context.user_id
        or admission.public_run_id != run_input.run_id
    ):
        raise conversation_not_found()
    if admission.existing is not None:
        return admission.existing
    parsed = parse_start_input(run_input, conversation.id)
    if conversation.source == INDEPENDENT_JOB_SOURCE:
        raise HTTPException(409, "Independent job conversations accept only approval resumes")
    if await conversation_run_service.get_latest_interrupted_run(
        db, conversation_id=conversation.id, context=context
    ):
        raise HTTPException(409, "Resolve the pending approval before sending another input")
    agent_id = await chat_service.prepare_executable_conversation(
        db, conversation.id, context, commit_policy_snapshot=False
    )
    cfg = await resolve_agent_context(
        db, conversation.id, user, context=context, checkpoint_id=parsed.checkpoint_id
    )
    if parsed.operation == "edit" or parsed.operation == "regenerate":
        from app.services.ag_ui_fork import accept_fork

        assert parsed.checkpoint_id is not None  # noqa: S101 - validated fork controls
        return await accept_fork(
            db=db,
            context=context,
            user=user,
            conversation=conversation,
            request=request,
            admission=admission,
            operation=parsed.operation,
            cfg=cfg,
            checkpoint_id=parsed.checkpoint_id,
            expected_head=parsed.expected_head,
            input_payload=parsed.input_payload,
            attachment_ids=parsed.attachment_ids,
        )

    payload, attachments = parsed.input_payload, parsed.attachment_ids
    request_id = "agui:" + hashlib.sha256(run_input.run_id.encode()).hexdigest()
    if parsed.retry_input_id is not None:
        from app.services.ag_ui_retry import load_retry_start

        payload, attachments = await load_retry_start(
            db,
            context=context,
            conversation=conversation,
            input_id=parsed.retry_input_id,
            public_run_id=admission.public_run_id,
        )
        request_id = f"agui-retry:{parsed.retry_input_id.hex}"
    # The receipt targets an immutable input ID. Queue edits may change the text,
    # but must never replace its graph message or manufacture an omitted-media note.
    input_id, omitted_media = _queued_message_identity(payload)
    if omitted_media:
        request_id += OMITTED_MEDIA_SUFFIX
    await authorize_start_attachments(
        db, context, conversation.id, attachments, require_linked=parsed.operation == "retry"
    )
    frozen = await freeze_queued_resource_context_payload(
        db,
        ResourceContextScope(context, agent_id, conversation.id),
        QueuedResourceContextInput(conversation.id, request_id, payload),
    )
    enqueued = await conversation_run_queue_service.enqueue_input_with_result(
        db,
        conversation_id=conversation.id,
        context=context,
        client_request_id=request_id,
        source="chat",
        input_payload=frozen,
        attachment_ids=attachments,
        checkpoint_id=None,
        priority=100 if parsed.strategy == "interrupt" else 0,
        input_id=input_id,
    )
    if not enqueued.created:
        # A matching AG-UI retry returned its original receipt before reaching this point.
        # A legacy queue entry or a retry already submitted under a different public run
        # cannot become a second command for the same input.
        raise HTTPException(409, "This conversation input was already submitted")
    await apply_start_effects(
        db,
        context=context,
        user=user,
        conversation=conversation,
        request=request,
        message_preview=input_preview(payload),
        attachment_ids=attachments,
        source="ag_ui",
        cfg=cfg,
        queued_input=enqueued.input,
    )
    if parsed.strategy == "interrupt":
        active = await interrupt_active_for_input(
            db, conversation_id=conversation.id, context=context, user=user, request=request
        )
        if active is not None:
            request.state.ag_ui_predecessor_id = active.id
    return await bind_command(db, admission, input_id=enqueued.input.id)


async def wake_queued_start(request: Request, conversation_id: UUID) -> None:
    """Wake committed work locally; the durable queue and lease supervise it across hosts."""
    predecessor_id = getattr(request.state, "ag_ui_predecessor_id", None)
    if predecessor_id is not None:
        from app.services.conversation_run_worker import get_run_task_registry

        get_run_task_registry().request_cancel(predecessor_id, reason="steer")
    else:
        from app.services.conversation_run_queue_worker import dispatch_next_for_conversation

        await dispatch_next_for_conversation(conversation_id)
