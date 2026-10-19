"""``run.start`` request validation and canonical load/lock blocks.

Each block is a verbatim, ordered slice of the original
``conversation_agent_protocol_commands._handle_run_start_command`` body.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_scope import require_conversation_agent_id
from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.exceptions import ConflictError
from app.models.conversation import Conversation
from app.routers.conversation_agent_protocol_attachments import (
    attachment_ids_from_protocol_input,
    input_without_protocol_attachments,
)
from app.routers.conversation_agent_protocol_contracts import AgentCommandRequest, command_error
from app.routers.conversation_agent_protocol_runtime import (
    SUPPORTED_MULTITASK_STRATEGIES,
    checkpoint_id,
    command_multitask_strategy,
)
from app.services import chat_service
from app.services.chat_resource_context_integration import freeze_resource_context_payload
from app.services.chat_resource_context_payload import frozen_resource_context_from_payload
from app.services.chat_resource_context_sources import ResourceContextScope
from app.services.conversation_stream_service import resolve_agent_context

if TYPE_CHECKING:
    from app.agent_runtime.runtime_config import AgentConfig
    from app.schemas.chat_resource_context import FrozenChatResourceContext
    from app.services.conversation_run_service import RunSource


@dataclass(frozen=True, slots=True)
class RunStartEntry:
    """The original ``_handle_run_start_command`` keyword arguments."""

    conversation: Conversation
    command: AgentCommandRequest
    request: Request
    db: AsyncSession
    user: CurrentUser
    context: OrgContext
    start_run: Callable[..., Awaitable[Any]]
    executor_fn: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class RunStartValidated:
    """Locals produced by the validation block, in original assignment order."""

    strategy: str
    input_payload: dict[str, JsonValue]
    attachment_ids: list[uuid.UUID]
    resolved_checkpoint_id: str | None
    runtime_input_payload: dict[str, JsonValue]
    context_scope: ResourceContextScope
    direct_context: FrozenChatResourceContext | None
    run_source: RunSource


def _resource_context_error(command: AgentCommandRequest, exc: HTTPException) -> JSONResponse:
    code = "RESOURCE_CONTEXT_NOT_FOUND" if exc.status_code == 404 else "RESOURCE_CONTEXT_INVALID"
    return command_error(
        command,
        code=code,
        message=str(exc.detail),
        status_code=exc.status_code,
    )


async def validate_run_start(entry: RunStartEntry) -> RunStartValidated | JSONResponse:
    command, conversation, db, context = entry.command, entry.conversation, entry.db, entry.context
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
    context_scope = ResourceContextScope(
        context=context,
        agent_id=require_conversation_agent_id(conversation),
        conversation_id=conversation.id,
    )
    direct_context = None
    if strategy == "reject":
        try:
            runtime_input_payload = await freeze_resource_context_payload(
                db, context_scope, runtime_input_payload
            )
        except HTTPException as exc:
            return _resource_context_error(command, exc)
        direct_context = frozen_resource_context_from_payload(runtime_input_payload)
    run_source = "chat"
    if strategy != "reject" and resolved_checkpoint_id:
        return command_error(
            command,
            code="QUEUED_FORK_UNSUPPORTED",
            message="Queued edit and regenerate requests are not supported.",
        )
    return RunStartValidated(
        strategy=strategy,
        input_payload=input_payload,
        attachment_ids=attachment_ids,
        resolved_checkpoint_id=resolved_checkpoint_id,
        runtime_input_payload=runtime_input_payload,
        context_scope=context_scope,
        direct_context=direct_context,
        run_source=run_source,
    )


async def lock_run_start(
    entry: RunStartEntry, resolved_checkpoint_id: str | None
) -> AgentConfig | JSONResponse:
    command, conversation, db, user = entry.command, entry.conversation, entry.db, entry.user
    context = entry.context
    try:
        await chat_service.prepare_executable_conversation(db, conversation.id, context)
        cfg = await resolve_agent_context(
            db,
            conversation.id,
            user,
            checkpoint_id=resolved_checkpoint_id,
            context=context,
        )
        await db.commit()
    except ConflictError as exc:
        return command_error(
            command,
            code=exc.code,
            message=exc.message,
            status_code=exc.status,
        )
    return cfg
