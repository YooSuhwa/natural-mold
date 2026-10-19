"""``run.start`` checkpoint/fork seed block, protocol message helpers, ``input.respond``."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from langchain_core.messages import BaseMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_scope import require_conversation_agent_id
from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.exceptions import ConflictError
from app.models.conversation import Conversation
from app.routers.conversation_agent_protocol_consent import apply_session_consent_decisions
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
    resume_decision_metadata,
    resume_run_interrupt_id,
    validate_resume_payload,
)
from app.routers.conversation_agent_protocol_resume_redaction import (
    RedactedResumeArgsUnavailable,
    restore_redacted_resume_payload,
)
from app.routers.conversation_agent_protocol_runtime import cfg_agent_uuid
from app.routers.conversation_protocol_messages import (
    messages_from_protocol_input as _messages_from_protocol_input,
)
from app.routers.conversation_protocol_response import _wait_for_interrupted_parent_run
from app.routers.conversation_protocol_start import RunStartEntry, RunStartValidated
from app.routers.session_consent_scope import SessionConsentPayload, SessionConsentTarget
from app.services import chat_service, conversation_run_service
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from app.services.chat_resource_context_payload import apply_frozen_resource_context
from app.services.conversation_audit_service import record_conversation_audit
from app.services.conversation_stream_service import resolve_agent_context

if TYPE_CHECKING:
    from app.services.conversation_run_service import RunSource

StartConversationRun = Callable[..., Awaitable[Any]]
AgentStreamExecutor = Callable[..., Any]


def _commands_attr(name: str) -> Any:
    """Resolve a patchable name through the protocol commands facade at call time."""
    from app.routers import conversation_agent_protocol_commands

    return getattr(conversation_agent_protocol_commands, name)


async def seed_run_start(
    entry: RunStartEntry, validated: RunStartValidated
) -> tuple[dict[str, Any], RunSource]:
    conversation, resolved_checkpoint_id = entry.conversation, validated.resolved_checkpoint_id
    runtime_input_payload: dict[str, Any] = validated.runtime_input_payload
    attachment_ids, direct_context = validated.attachment_ids, validated.direct_context
    run_source = validated.run_source
    if resolved_checkpoint_id:
        append_messages = _messages_from_protocol_input(runtime_input_payload)
        runtime_input_payload = await _commands_attr("_fork_overwrite_input")(
            db=entry.db,
            context=entry.context,
            conversation_id=conversation.id,
            checkpoint_id=resolved_checkpoint_id,
            append_messages=append_messages,
            drop_trailing_assistant=not append_messages and not attachment_ids,
        )
        if direct_context is not None:
            runtime_input_payload = apply_frozen_resource_context(
                runtime_input_payload, direct_context
            )
        run_source = "edit" if append_messages or attachment_ids else "regenerate"
    return runtime_input_payload, run_source


async def _fork_overwrite_input(
    *,
    db: AsyncSession,
    context: OrgContext,
    conversation_id: uuid.UUID,
    checkpoint_id: str,
    append_messages: list[BaseMessage],
    drop_trailing_assistant: bool = False,
) -> dict[str, Any]:
    from app.agent_runtime.conversation_checkpointer import conversation_checkpointer
    from app.services.thread_branch_service import build_fork_overwrite_input

    return await build_fork_overwrite_input(
        await conversation_checkpointer(str(conversation_id), context, db=db),
        str(conversation_id),
        checkpoint_id,
        append=append_messages,
        drop_trailing_assistant=drop_trailing_assistant,
    )


async def _handle_input_respond_command(
    *,
    conversation: Conversation,
    command: AgentCommandRequest,
    request: Request,
    db: AsyncSession,
    user: CurrentUser,
    context: OrgContext,
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

    parent_run = await conversation_run_service.get_latest_interrupted_run(
        db,
        conversation_id=conversation.id,
        context=context,
    )
    if parent_run is None:
        parent_run = await _wait_for_interrupted_parent_run(
            db, conversation_id=conversation.id, context=context
        )
        if parent_run is not None:
            # 워커 전이는 별도 세션 커밋이라, 대기 초반에 identity map에 적재된
            # 인스턴스는 status/interrupt_id가 stale일 수 있다(R2) — PK는 정확하나
            # 이후 필드를 읽는 코드가 오동작하지 않게 새로고침한다.
            await db.refresh(parent_run)
    if parent_run is None:
        return command_error(
            command,
            code="RESUME_NOT_FOUND",
            message="Resume run requires an interrupted parent run",
        )

    tasks = await load_pending_interrupt_tasks(db, conversation, user_id=user.id, context=context)
    validation_error = validate_resume_payload(resume, interrupts_from_tasks(tasks))
    if validation_error is not None:
        return validation_error.response(command)
    try:
        input_payload = await restore_redacted_resume_payload(
            conversation=conversation,
            resume=resume,
            pending_interrupts=interrupts_from_tasks(tasks),
            db=db,
            context=context,
        )
    except RedactedResumeArgsUnavailable:
        return command_error(
            command,
            code="REDACTED_EDIT_REQUIRES_REPLACEMENT",
            message="Edited tool args still contain redacted placeholders that cannot be restored",
        )
    run_interrupt_id = resume_run_interrupt_id(resume, parent_run.interrupt_id)

    try:
        await chat_service.prepare_resumed_conversation(db, conversation.id, context, resume)
    except ConflictError as exc:
        return command_error(
            command,
            code=exc.code,
            message=exc.message,
            status_code=exc.status,
        )
    # Session consent must be durable before the resumed Agent is rebuilt.
    consented_tools = await apply_session_consent_decisions(
        db,
        SessionConsentTarget(conversation.id, context),
        SessionConsentPayload(resume, interrupts_from_tasks(tasks)),
    )
    await db.commit()
    await chat_service.require_executable_conversation(db, conversation.id, context)
    cfg = await resolve_agent_context(db, conversation.id, user, context=context)
    await db.commit()

    try:
        accepted = await accept_owned_execution(
            db,
            OwnedExecutionRequest(
                conversation_id=conversation.id,
                root_agent_id=cfg_agent_uuid(conversation),
                context=context,
                source="chat",
                run_source="resume",
                input_preview=None,
                parent_run_id=parent_run.id,
                interrupt_id=run_interrupt_id,
                run_metadata={
                    "protocol": "langgraph_v3",
                    "command_id": command.id,
                    "parent_run_id": str(parent_run.id),
                    **resume_decision_metadata(resume, interrupts_from_tasks(tasks)),
                },
            ),
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
            return command_error(command, code="MULTITASK_REJECTED", message=str(exc.detail))
        raise
    await chat_service.touch_conversation(db, conversation.id, context)
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.message_resume",
        conversation_id=conversation.id,
        agent_id=require_conversation_agent_id(conversation),
        metadata={
            "source": "langgraph_protocol",
            "interrupt_id": run_interrupt_id,
            **({"consented_tools": consented_tools} if consented_tools else {}),
        },
    )
    run_id = accepted.run_id
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
