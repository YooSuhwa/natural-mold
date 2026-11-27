"""Accept and recover an owned edit or regeneration from an immutable checkpoint."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, Request
from langchain_core.messages import HumanMessage
from langgraph.types import Overwrite
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.attachment_inputs import FORK_APPEND_COUNT
from app.agent_runtime.conversation_checkpointer import conversation_checkpointer
from app.agent_runtime.runtime_config import AgentConfig
from app.authz.conversation_scope import require_conversation_agent_id
from app.authz.org_context import OrgContext
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import JsonValue
from app.schemas.conversation_graph_input import OMITTED_MEDIA_KEY, is_omitted_user_message
from app.services.ag_ui_commands import (
    PROJECTION_VERSION,
    AgUiRunBinding,
    CommandAdmission,
    bind_command,
)
from app.services.ag_ui_fork_branch import FORK_SELECTION_KEY
from app.services.ag_ui_fork_checkpoints import ForkOperation, load_fork_checkpoint
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from app.services.chat_resource_context_integration import (
    freeze_resource_context_payload,
    reauthorize_resource_context_payload,
)
from app.services.chat_resource_context_sources import ResourceContextScope


def _input_kwargs(message: dict[str, JsonValue]) -> dict[str, bool]:
    if "additional_kwargs" not in message:
        return {}
    if is_omitted_user_message(message):
        return {OMITTED_MEDIA_KEY: True}
    raise HTTPException(status_code=422, detail="Invalid fork message metadata")


def _user_delta(input_payload: dict[str, JsonValue], operation: ForkOperation) -> str | None:
    messages = input_payload.get("messages")
    if operation == "regenerate" and (messages is None or messages == []):
        return None
    if operation == "edit" and isinstance(messages, list) and len(messages) == 1:
        message = messages[0]
        if (
            isinstance(message, dict)
            and message.get("role") == "user"
            and set(message) <= {"id", "role", "content", "additional_kwargs"}
            and isinstance(message.get("id"), str)
            and isinstance(content := message.get("content"), str)
        ):
            _input_kwargs(message)
            return content
    raise HTTPException(status_code=422, detail="Invalid fork input")


async def accept_fork(
    *,
    db: AsyncSession,
    context: OrgContext,
    user: CurrentUser,
    conversation: Conversation,
    request: Request,
    cfg: AgentConfig,
    admission: CommandAdmission,
    operation: ForkOperation,
    checkpoint_id: str,
    expected_head: str | None,
    input_payload: dict[str, JsonValue],
    attachment_ids: list[uuid.UUID],
) -> AgUiRunBinding:
    from app.services.ag_ui_deferred_delivery import seal_ag_ui_delivery
    from app.services.ag_ui_start_effects import apply_start_effects, authorize_start_attachments

    agent_id = require_conversation_agent_id(conversation)
    preview = _user_delta(input_payload, operation)
    if operation == "regenerate" and (attachment_ids or "resource_context" in input_payload):
        raise HTTPException(status_code=422, detail="Regeneration accepts no new input")
    await authorize_start_attachments(db, context, conversation.id, attachment_ids)
    saver = await conversation_checkpointer(str(conversation.id), context, db=db)
    head, _ = await load_fork_checkpoint(
        saver,
        thread_id=str(conversation.id),
        tenant_id=context.tenant_id,
        selected=conversation.active_branch_checkpoint_id,
        target=checkpoint_id,
        expected=expected_head,
        operation=operation,
    )
    frozen = await freeze_resource_context_payload(
        db, ResourceContextScope(context, agent_id, conversation.id), input_payload
    )
    accepted = await accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=conversation.id,
            root_agent_id=agent_id,
            context=context,
            source="chat",
            run_source=operation,
            run_metadata={"ag_ui_projection_version": PROJECTION_VERSION},
            input_preview=preview,
        ),
    )
    private: dict[str, JsonValue] = {
        "expected_head": head,
        "input": frozen,
        "attachment_ids": [str(value) for value in attachment_ids],
    }
    encrypted = await seal_ag_ui_delivery(
        db,
        context=context,
        conversation_id=conversation.id,
        public_run_id=admission.public_run_id,
        run_id=accepted.run_id,
        source=operation,
        parent_run_id=None,
        checkpoint_id=checkpoint_id,
        payload=private,
    )
    binding = await bind_command(
        db,
        admission,
        run_id=accepted.run_id,
        checkpoint_id=checkpoint_id,
        delivery_encrypted=encrypted,
    )
    await apply_start_effects(
        db,
        context=context,
        user=user,
        conversation=conversation,
        request=request,
        cfg=cfg,
        run_id=accepted.run_id,
        message_preview=preview,
        attachment_ids=attachment_ids,
        source="ag_ui",
    )
    return binding


async def prepare_fork_delivery(
    db: AsyncSession,
    *,
    run: ConversationRun,
    conversation: Conversation,
    context: OrgContext,
    user: CurrentUser,
    cfg: AgentConfig,
    checkpoint_id: str,
    payload: JsonValue,
) -> tuple[dict[str, Any], list[uuid.UUID]]:
    from app.services.ag_ui_start_effects import authorize_start_attachments

    source = run.source
    actor = (context.user_id, context.org_id, context.tenant_id)
    if (
        source not in {"edit", "regenerate"}
        or not checkpoint_id
        or cfg.checkpoint_id != checkpoint_id
        or run.conversation_id != conversation.id
        or cfg.thread_id != str(conversation.id)
        or cfg.org_context != context
        or run.parent_run_id is not None
        or run.status != "queued"
        or not run.is_active
        or run.cancel_requested_at is not None
        or run.agent_id != conversation.agent_id
        or (conversation.user_id, conversation.org_id, conversation.tenant_id) != actor
        or (run.user_id, run.org_id, run.tenant_id) != actor
        or user.id != context.user_id
        or not isinstance(payload, dict)
    ):
        raise ValueError("Invalid fork delivery scope")
    operation: ForkOperation = "edit" if source == "edit" else "regenerate"
    head = payload.get("expected_head")
    input_payload = payload.get("input")
    file_ids = payload.get("attachment_ids")
    if (
        not isinstance(head, str)
        or not head
        or not isinstance(input_payload, dict)
        or not isinstance(file_ids, list)
        or not all(isinstance(value, str) for value in file_ids)
    ):
        raise ValueError("Invalid stored fork input")
    _user_delta(input_payload, operation)
    attachment_ids = [uuid.UUID(value) for value in file_ids if isinstance(value, str)]
    await authorize_start_attachments(
        db, context, conversation.id, attachment_ids, require_linked=True
    )
    scope = ResourceContextScope(
        context, require_conversation_agent_id(conversation), conversation.id
    )
    runtime = await reauthorize_resource_context_payload(db, scope, input_payload)
    append: list[HumanMessage] = []
    messages = runtime.get("messages", [])
    if not isinstance(messages, list):
        raise ValueError("Invalid stored fork delta")
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            raise ValueError("Invalid stored fork role")
        content = message.get("content")
        mid = message.get("id")
        if not isinstance(content, str) or (mid is not None and not isinstance(mid, str)):
            raise ValueError("Invalid stored fork message")
        append.append(
            HumanMessage(content=content, id=mid, additional_kwargs=_input_kwargs(message))
        )
    saver = await conversation_checkpointer(str(conversation.id), context, db=db)
    _, history = await load_fork_checkpoint(
        saver,
        thread_id=str(conversation.id),
        tenant_id=context.tenant_id,
        selected=conversation.active_branch_checkpoint_id,
        target=checkpoint_id,
        expected=head,
        operation=operation,
    )
    # The dispatcher commits this under its conversation lock before the
    # physical claim. Only an authenticated, still-owned delivery enters here.
    run.metadata_json = {
        **(run.metadata_json or {}),
        FORK_SELECTION_KEY: conversation.active_branch_checkpoint_id,
    }
    return {
        "messages": Overwrite(value=[*history, *append]),
        FORK_APPEND_COUNT: len(append),
    }, attachment_ids
