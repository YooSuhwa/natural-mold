"""``run.start`` queued-input and direct-run persistence blocks.

Each block is a verbatim, ordered slice of the original
``conversation_agent_protocol_commands._handle_run_start_command`` body.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.exceptions import ConflictError
from app.routers.conversation_agent_protocol_contracts import command_error
from app.routers.conversation_agent_protocol_runtime import cfg_agent_uuid
from app.routers.conversation_protocol_start import (
    RunStartEntry,
    RunStartValidated,
    _resource_context_error,
)
from app.services import conversation_run_queue_service, conversation_run_service
from app.services.ag_ui_start_effects import apply_start_effects
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from app.services.chat_resource_context_integration import (
    QueuedResourceContextInput,
    freeze_queued_resource_context_payload,
)
from app.services.conversation_run_queue_promotion import interrupt_active_for_input

if TYPE_CHECKING:
    from app.agent_runtime.runtime_config import AgentConfig
    from app.models.conversation_run import ConversationRun
    from app.models.conversation_run_input import ConversationRunInput
    from app.services.conversation_run_queue_service import EnqueuedConversationInput
    from app.services.conversation_run_service import RunSource

QueuedRunStart = tuple[
    "ConversationRunInput", "EnqueuedConversationInput", "ConversationRun | None"
]


async def persist_queued_run_start(
    entry: RunStartEntry,
    validated: RunStartValidated,
    runtime_input_payload: dict[str, Any],
    preview: str | None,
    cfg: AgentConfig,
) -> QueuedRunStart | JSONResponse:
    conversation, command, request, db = entry.conversation, entry.command, entry.request, entry.db
    user, context, strategy = entry.user, entry.context, validated.strategy
    context_scope, attachment_ids = validated.context_scope, validated.attachment_ids
    pending_interrupt = await conversation_run_service.get_latest_interrupted_run(
        db,
        conversation_id=conversation.id,
        context=context,
    )
    if pending_interrupt is not None:
        return command_error(
            command,
            code="QUEUE_HITL_PENDING",
            message="Resolve the pending approval before queueing another input.",
        )
    client_request_id = command.params.client_request_id or str(
        command.id if command.id is not None else uuid.uuid4()
    )
    try:
        runtime_input_payload = await freeze_queued_resource_context_payload(
            db,
            context_scope,
            QueuedResourceContextInput(
                conversation_id=conversation.id,
                client_request_id=client_request_id,
                input_payload=runtime_input_payload,
            ),
        )
    except HTTPException as exc:
        return _resource_context_error(command, exc)
    enqueue_result = await conversation_run_queue_service.enqueue_input_with_result(
        db,
        conversation_id=conversation.id,
        context=context,
        client_request_id=client_request_id,
        source="chat",
        input_payload=runtime_input_payload,
        attachment_ids=attachment_ids,
        checkpoint_id=None,
        priority=100 if strategy == "interrupt" else 0,
    )
    queued = enqueue_result.input
    await apply_start_effects(
        db,
        context=context,
        user=user,
        conversation=conversation,
        request=request,
        message_preview=preview,
        attachment_ids=attachment_ids,
        source="langgraph_protocol",
        cfg=cfg,
        queued_input=queued if enqueue_result.created else None,
    )
    active = None
    if strategy == "interrupt" and enqueue_result.created:
        active = await interrupt_active_for_input(
            db, conversation_id=conversation.id, context=context, user=user, request=request
        )
    await db.commit()
    return queued, enqueue_result, active


async def persist_direct_run_start(
    entry: RunStartEntry,
    validated: RunStartValidated,
    run_source: RunSource,
    preview: str | None,
    runtime_input_payload: dict[str, Any],
) -> ConversationRun | JSONResponse:
    conversation, command, db, context = entry.conversation, entry.command, entry.db, entry.context
    resolved_checkpoint_id, attachment_ids = (
        validated.resolved_checkpoint_id,
        validated.attachment_ids,
    )
    try:
        accepted = await accept_owned_execution(
            db,
            OwnedExecutionRequest(
                conversation_id=conversation.id,
                root_agent_id=cfg_agent_uuid(conversation),
                context=context,
                source="chat",
                run_source=run_source,
                input_preview=preview,
                run_metadata={
                    "protocol": "langgraph_v3",
                    "command_id": command.id,
                    "assistant_id": command.params.assistant_id,
                    "checkpoint_id": resolved_checkpoint_id,
                },
            ),
        )
        run = await conversation_run_service.get_run_for_user(
            db,
            conversation_id=conversation.id,
            run_id=accepted.run_id,
            context=context,
        )
        if run is None:
            raise RuntimeError("accepted conversation run was not persisted")
        if resolved_checkpoint_id is None:
            await conversation_run_queue_service.persist_direct_input(
                db,
                context=context,
                run=run,
                client_request_id=command.params.client_request_id or str(uuid.uuid4()),
                input_payload=runtime_input_payload,
                attachment_ids=attachment_ids,
            )
    except ConflictError as exc:
        return command_error(
            command,
            code=exc.code,
            message=exc.message,
            status_code=exc.status,
        )
    except HTTPException as exc:
        if exc.status_code == 409:
            return command_error(
                command,
                code="MULTITASK_REJECTED",
                message=str(exc.detail),
            )
        raise
    return run
