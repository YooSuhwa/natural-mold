from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.executor import execute_agent_stream_langgraph, resume_agent_stream_langgraph
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.routers.conversation_agent_protocol_attachments import (
    attachment_ids_from_protocol_input,
    input_without_protocol_attachments,
)
from app.routers.conversation_agent_protocol_contracts import (
    AgentCommandParams,
    AgentCommandRequest,
    command_error,
    command_success,
)
from app.routers.conversation_agent_protocol_runtime import (
    SUPPORTED_MULTITASK_STRATEGIES,
    cfg_agent_uuid,
    checkpoint_id,
    command_multitask_strategy,
    input_preview,
)
from app.services import chat_service, conversation_run_service
from app.services.conversation_audit_service import record_conversation_audit
from app.services.conversation_stream_service import resolve_agent_context

StartConversationRun = Callable[..., Awaitable[Any]]
AgentStreamExecutor = Callable[..., Any]


def _resume_payload(params: AgentCommandParams) -> tuple[Any, str | None]:
    if params.responses is not None:
        responses = {entry.interrupt_id: entry.response for entry in params.responses}
        first = params.responses[0]
        return responses, first.interrupt_id
    return params.response, params.interrupt_id


async def _handle_run_start_command(
    *,
    conversation: Conversation,
    command: AgentCommandRequest,
    request: Request,
    db: AsyncSession,
    user: CurrentUser,
    start_run: StartConversationRun,
    executor_fn: AgentStreamExecutor,
) -> JSONResponse:
    strategy = command_multitask_strategy(command)
    if strategy not in SUPPORTED_MULTITASK_STRATEGIES:
        return command_error(
            command,
            code="UNSUPPORTED_MULTITASK_STRATEGY",
            message=f"Unsupported multitask strategy: {strategy}",
        )

    input_payload = command.params.input or {}
    attachment_ids = attachment_ids_from_protocol_input(input_payload)
    runtime_input_payload = input_without_protocol_attachments(input_payload)
    preview = input_preview(input_payload)
    cfg = await resolve_agent_context(
        db,
        conversation.id,
        user,
        checkpoint_id=checkpoint_id(command),
    )
    if preview:
        await chat_service.maybe_set_auto_title(db, conversation.id, preview)
    await chat_service.touch_conversation(db, conversation.id)
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.message_send",
        conversation_id=conversation.id,
        agent_id=cfg_agent_uuid(conversation),
        metadata={
            "content_length": len(preview or ""),
            "attachment_count": len(attachment_ids),
            "source": "langgraph_protocol",
        },
    )
    try:
        run = await conversation_run_service.create_run(
            db,
            conversation_id=conversation.id,
            agent_id=cfg_agent_uuid(conversation),
            user_id=user.id,
            source="chat",
            input_preview=preview,
            metadata={
                "protocol": "langgraph_v3",
                "command_id": command.id,
                "assistant_id": command.params.assistant_id,
            },
        )
    except HTTPException as exc:
        if exc.status_code == 409:
            return command_error(
                command,
                code="MULTITASK_REJECTED",
                message=str(exc.detail),
            )
        raise
    run_id = run.id
    if attachment_ids:
        await chat_service.link_attachments_to_conversation(
            db,
            conversation_id=conversation.id,
            user_id=user.id,
            attachment_ids=attachment_ids,
        )
    await db.commit()

    await start_run(
        run_id=run_id,
        conversation_id=conversation.id,
        cfg=cfg,
        user=user,
        input_payload=runtime_input_payload,
        moldy_source="chat",
        executor_fn=executor_fn,
    )
    return command_success(
        command,
        conversation=conversation,
        thread_id=str(conversation.id),
        run_id=str(run_id),
    )


async def _handle_input_respond_command(
    *,
    conversation: Conversation,
    command: AgentCommandRequest,
    request: Request,
    db: AsyncSession,
    user: CurrentUser,
    start_run: StartConversationRun,
    executor_fn: AgentStreamExecutor,
) -> JSONResponse:
    resume_payload, interrupt_id = _resume_payload(command.params)
    if not interrupt_id:
        return command_error(
            command,
            code="INVALID_INPUT_RESPOND",
            message="input.respond requires interrupt_id or responses[0].interrupt_id",
        )

    cfg = await resolve_agent_context(db, conversation.id, user)
    await chat_service.touch_conversation(db, conversation.id)
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.message_resume",
        conversation_id=conversation.id,
        agent_id=uuid.UUID(cfg.agent_id) if cfg.agent_id else None,
        metadata={"source": "langgraph_protocol", "interrupt_id": interrupt_id},
    )
    parent_run = await conversation_run_service.get_latest_interrupted_run(
        db,
        conversation_id=conversation.id,
        user_id=user.id,
    )
    if parent_run is None:
        return command_error(
            command,
            code="RESUME_NOT_FOUND",
            message="Resume run requires an interrupted parent run",
        )

    try:
        run = await conversation_run_service.create_run(
            db,
            conversation_id=conversation.id,
            agent_id=cfg_agent_uuid(conversation),
            user_id=user.id,
            source="resume",
            input_preview=None,
            parent_run_id=parent_run.id,
            interrupt_id=interrupt_id,
            metadata={
                "protocol": "langgraph_v3",
                "command_id": command.id,
                "parent_run_id": str(parent_run.id),
            },
        )
    except HTTPException as exc:
        if exc.status_code == 409:
            return command_error(command, code="MULTITASK_REJECTED", message=str(exc.detail))
        raise
    run_id = run.id
    await db.commit()

    await start_run(
        run_id=run_id,
        conversation_id=conversation.id,
        cfg=cfg,
        user=user,
        input_payload=resume_payload,
        moldy_source="resume",
        executor_fn=executor_fn,
    )
    return command_success(
        command,
        conversation=conversation,
        thread_id=str(conversation.id),
        run_id=str(run_id),
    )


async def handle_thread_command(
    *,
    conversation: Conversation,
    command: AgentCommandRequest,
    request: Request,
    db: AsyncSession,
    user: CurrentUser,
    start_run: StartConversationRun,
    run_start_executor: AgentStreamExecutor = execute_agent_stream_langgraph,
    input_respond_executor: AgentStreamExecutor = resume_agent_stream_langgraph,
) -> JSONResponse:
    if command.method == "run.start":
        return await _handle_run_start_command(
            conversation=conversation,
            command=command,
            request=request,
            db=db,
            user=user,
            start_run=start_run,
            executor_fn=run_start_executor,
        )
    if command.method == "input.respond":
        return await _handle_input_respond_command(
            conversation=conversation,
            command=command,
            request=request,
            db=db,
            user=user,
            start_run=start_run,
            executor_fn=input_respond_executor,
        )
    return command_error(
        command,
        code="UNSUPPORTED_COMMAND",
        message=f"Unsupported command method: {command.method}",
    )
