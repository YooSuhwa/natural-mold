from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.executor import execute_agent_stream_langgraph, resume_agent_stream_langgraph
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.routers.conversation_agent_protocol_attachments import (
    attachment_ids_from_protocol_input,
    input_without_protocol_attachments,
)
from app.routers.conversation_agent_protocol_contracts import (
    AgentCommandRequest,
    command_error,
    command_success,
)
from app.routers.conversation_agent_protocol_interrupts import (
    interrupts_from_tasks,
    load_pending_interrupt_tasks,
)
from app.routers.conversation_agent_protocol_resume import (
    build_resume_payload,
    resume_run_interrupt_id,
    validate_resume_payload,
)
from app.routers.conversation_agent_protocol_resume_redaction import (
    RedactedResumeArgsUnavailable,
    restore_redacted_resume_payload,
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
    resolved_checkpoint_id = checkpoint_id(command)
    runtime_input_payload = input_without_protocol_attachments(input_payload)
    run_source = "chat"
    if resolved_checkpoint_id:
        append_messages = _messages_from_protocol_input(runtime_input_payload)
        runtime_input_payload = await _fork_overwrite_input(
            conversation_id=conversation.id,
            checkpoint_id=resolved_checkpoint_id,
            append_messages=append_messages,
            drop_trailing_assistant=not append_messages and not attachment_ids,
        )
        run_source = "edit" if append_messages or attachment_ids else "regenerate"
    preview = input_preview(input_payload)
    cfg = await resolve_agent_context(
        db,
        conversation.id,
        user,
        checkpoint_id=resolved_checkpoint_id,
    )
    if conversation.source == "draft":
        await chat_service.promote_draft_conversation(
            db,
            conversation,
            title_from_content=preview,
        )
    elif preview:
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
            source=run_source,
            input_preview=preview,
            metadata={
                "protocol": "langgraph_v3",
                "command_id": command.id,
                "assistant_id": command.params.assistant_id,
                "checkpoint_id": resolved_checkpoint_id,
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
        if cfg.runtime_profile == "skill_builder" and cfg.draft_workspace_path:
            # 빌더 챗 (AD-2/§6-3): 이번 턴 첨부를 드래프트 워크스페이스
            # ``inputs/``로 **복사**한다 — uploads 마운트 금지.
            from app.services import skill_draft_workspace

            await skill_draft_workspace.copy_conversation_attachments_to_inputs(
                db,
                storage_path=cfg.draft_workspace_path,
                attachment_ids=attachment_ids,
                user_id=user.id,
            )
    await db.commit()

    await start_run(
        run_id=run_id,
        conversation_id=conversation.id,
        cfg=cfg,
        user=user,
        input_payload=runtime_input_payload,
        moldy_source=run_source,
        executor_fn=executor_fn,
        attachment_ids=attachment_ids,
    )
    return command_success(
        command,
        conversation=conversation,
        thread_id=str(conversation.id),
        run_id=str(run_id),
    )


async def _fork_overwrite_input(
    *,
    conversation_id: uuid.UUID,
    checkpoint_id: str,
    append_messages: list[BaseMessage],
    drop_trailing_assistant: bool = False,
) -> dict[str, Any]:
    from app.agent_runtime.checkpointer import get_checkpointer
    from app.services.thread_branch_service import build_fork_overwrite_input

    return await build_fork_overwrite_input(
        get_checkpointer(),
        str(conversation_id),
        checkpoint_id,
        append=append_messages,
        drop_trailing_assistant=drop_trailing_assistant,
    )


def _messages_from_protocol_input(input_payload: dict[str, Any]) -> list[BaseMessage]:
    messages = input_payload.get("messages")
    if not isinstance(messages, list):
        return []

    converted: list[BaseMessage] = []
    for raw in messages:
        if not isinstance(raw, dict):
            continue
        msg = _message_from_protocol_mapping(raw)
        if msg is not None:
            converted.append(msg)
    return converted


def _message_from_protocol_mapping(raw: dict[str, Any]) -> BaseMessage | None:
    role = raw.get("role") or raw.get("type")
    content = raw.get("content", "")
    if not isinstance(content, str | list):
        content = str(content)

    message_id = raw.get("id")
    kwargs = {"id": message_id} if isinstance(message_id, str) and message_id else {}

    if role in {"human", "user"}:
        return HumanMessage(content=content, **kwargs)
    if role in {"ai", "assistant"}:
        return AIMessage(content=content, **kwargs)
    if role == "system":
        return SystemMessage(content=content, **kwargs)
    return None


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
    resume = build_resume_payload(command.params)
    if not resume.interrupt_id:
        return command_error(
            command,
            code="INVALID_INPUT_RESPOND",
            message="input.respond requires interrupt_id or responses[0].interrupt_id",
        )

    cfg = await resolve_agent_context(db, conversation.id, user)
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

    tasks = await load_pending_interrupt_tasks(db, conversation, user_id=user.id)
    validation_error = validate_resume_payload(resume, interrupts_from_tasks(tasks))
    if validation_error is not None:
        return command_error(
            command,
            code=validation_error.code,
            message=validation_error.message,
        )
    try:
        input_payload = await restore_redacted_resume_payload(
            conversation=conversation,
            resume=resume,
            pending_interrupts=interrupts_from_tasks(tasks),
        )
    except RedactedResumeArgsUnavailable:
        return command_error(
            command,
            code="REDACTED_EDIT_REQUIRES_REPLACEMENT",
            message="Edited tool args still contain redacted placeholders that cannot be restored",
        )
    run_interrupt_id = resume_run_interrupt_id(resume, parent_run.interrupt_id)

    await chat_service.touch_conversation(db, conversation.id)
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.message_resume",
        conversation_id=conversation.id,
        agent_id=uuid.UUID(cfg.agent_id) if cfg.agent_id else None,
        metadata={"source": "langgraph_protocol", "interrupt_id": run_interrupt_id},
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
            interrupt_id=run_interrupt_id,
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
        input_payload=input_payload,
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
