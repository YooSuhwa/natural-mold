from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.executor import execute_agent_stream_langgraph, resume_agent_stream_langgraph
from app.authz.conversation_scope import require_conversation_agent_id
from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.exceptions import ConflictError
from app.models.conversation import Conversation
from app.routers.conversation_agent_protocol_attachments import (
    attachment_ids_from_protocol_input,
    input_without_protocol_attachments,
)
from app.routers.conversation_agent_protocol_consent import (
    apply_session_consent_decisions,
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
from app.routers.conversation_protocol_fork import (
    _fork_overwrite_input,
    _handle_input_respond_command,
    _message_from_protocol_mapping,
    _messages_from_protocol_input,
    seed_run_start,
)
from app.routers.conversation_protocol_persistence import (
    persist_direct_run_start,
    persist_queued_run_start,
)
from app.routers.conversation_protocol_response import (
    _RESUME_INTERRUPT_WAIT_INTERVAL_S,
    _RESUME_INTERRUPT_WAIT_TIMEOUT_S,
    _wait_for_interrupted_parent_run,
    dispatch_direct_run_start,
    dispatch_queued_run_start,
    finalize_direct_run_start,
)
from app.routers.conversation_protocol_start import (
    RunStartEntry,
    _resource_context_error,
    lock_run_start,
    validate_run_start,
)
from app.routers.session_consent_scope import SessionConsentPayload, SessionConsentTarget
from app.schemas.conversation_scope import ConversationAutoTitle, ConversationPromotion
from app.services import chat_service, conversation_run_queue_service, conversation_run_service
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from app.services.chat_resource_context_integration import (
    QueuedResourceContextInput,
    freeze_queued_resource_context_payload,
    freeze_resource_context_payload,
)
from app.services.chat_resource_context_payload import (
    apply_frozen_resource_context,
    frozen_resource_context_from_payload,
    resource_context_user_message,
)
from app.services.chat_resource_context_sources import ResourceContextScope
from app.services.conversation_audit_service import (
    record_conversation_audit,
    record_conversation_run_audit,
)
from app.services.conversation_stream_service import resolve_agent_context

StartConversationRun = Callable[..., Awaitable[Any]]
AgentStreamExecutor = Callable[..., Any]

__all__ = [
    "AIMessage",
    "BaseMessage",
    "HumanMessage",
    "SystemMessage",
    "QueuedResourceContextInput",
]
__all__ += ["ConversationAutoTitle", "ConversationPromotion", "ResourceContextScope"]
__all__ += ["SUPPORTED_MULTITASK_STRATEGIES", "_fork_overwrite_input", "_resource_context_error"]
__all__ += ["_message_from_protocol_mapping", "_messages_from_protocol_input"]
__all__ += ["apply_frozen_resource_context", "attachment_ids_from_protocol_input", "checkpoint_id"]
__all__ += ["command_multitask_strategy", "conversation_run_queue_service", "input_preview"]
__all__ += ["freeze_queued_resource_context_payload", "freeze_resource_context_payload"]
__all__ += ["frozen_resource_context_from_payload", "input_without_protocol_attachments"]
__all__ += ["record_conversation_run_audit", "resource_context_user_message"]
__all__ += [
    "_RESUME_INTERRUPT_WAIT_INTERVAL_S",
    "_RESUME_INTERRUPT_WAIT_TIMEOUT_S",
    "asyncio",
    "time",
]
__all__ += ["_wait_for_interrupted_parent_run", "RedactedResumeArgsUnavailable", "uuid"]
__all__ += ["SessionConsentPayload", "SessionConsentTarget", "apply_session_consent_decisions"]
__all__ += ["build_resume_payload", "resume_run_interrupt_id", "validate_resume_payload"]
__all__ += [
    "interrupts_from_tasks",
    "load_pending_interrupt_tasks",
    "restore_redacted_resume_payload",
]
__all__ += ["accept_owned_execution", "OwnedExecutionRequest", "ConflictError", "HTTPException"]
__all__ += [
    "cfg_agent_uuid",
    "command_success",
    "conversation_run_service",
    "resolve_agent_context",
]
__all__ += ["record_conversation_audit", "require_conversation_agent_id"]


async def _handle_run_start_command(
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
    entry = RunStartEntry(
        conversation=conversation,
        command=command,
        request=request,
        db=db,
        user=user,
        context=context,
        start_run=start_run,
        executor_fn=executor_fn,
    )
    validated = await validate_run_start(entry)
    if isinstance(validated, JSONResponse):
        return validated
    runtime_input_payload, run_source = await seed_run_start(entry, validated)
    preview = input_preview(validated.input_payload)
    cfg = await lock_run_start(entry, validated.resolved_checkpoint_id)
    if isinstance(cfg, JSONResponse):
        return cfg
    if validated.strategy != "reject":
        queued_start = await persist_queued_run_start(
            entry, validated, runtime_input_payload, preview, cfg
        )
        if isinstance(queued_start, JSONResponse):
            return queued_start
        return await dispatch_queued_run_start(entry, validated.strategy, queued_start)
    run = await persist_direct_run_start(
        entry, validated, run_source, preview, runtime_input_payload
    )
    if isinstance(run, JSONResponse):
        return run
    run_id = await finalize_direct_run_start(entry, validated.attachment_ids, preview, cfg, run)
    return await dispatch_direct_run_start(
        entry, run_id, cfg, runtime_input_payload, run_source, validated.attachment_ids
    )


async def handle_thread_command(
    *,
    conversation: Conversation,
    command: AgentCommandRequest,
    request: Request,
    db: AsyncSession,
    user: CurrentUser,
    context: OrgContext,
    start_run: StartConversationRun,
    run_start_executor: AgentStreamExecutor = execute_agent_stream_langgraph,
    input_respond_executor: AgentStreamExecutor = resume_agent_stream_langgraph,
) -> JSONResponse:
    # agent#can_run is decided once, in prepare_executable_conversation (start/fork).
    await chat_service.require_executable_conversation(db, conversation.id, context)
    if command.method == "run.start":
        return await _handle_run_start_command(
            conversation=conversation,
            command=command,
            request=request,
            db=db,
            user=user,
            context=context,
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
            context=context,
            start_run=start_run,
            executor_fn=input_respond_executor,
        )
    return command_error(
        command,
        code="UNSUPPORTED_COMMAND",
        message=f"Unsupported command method: {command.method}",
    )
