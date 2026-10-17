from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator, Iterable, Mapping
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import event_names
from app.agent_runtime.event_broker import EventBroker, slice_events_after
from app.agent_runtime.event_broker import registry as broker_registry
from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.agent_runtime.streaming import format_sse
from app.authz.conversation_agent_checks import bind_request_authz
from app.authz.conversation_scope import load_private_conversation, require_conversation_agent_id
from app.authz.org_context import OrgContext
from app.authz.policies import PRIVATE, authz_policy
from app.authz.service import AuthzService
from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_db,
    get_org_context,
    owned_conversation,
    verify_csrf,
)
from app.error_codes import (
    agent_not_found,
    conversation_not_found,
    resume_interrupt_pending,
    resume_not_found,
)
from app.models.conversation import Conversation
from app.models.message_event import MessageEvent
from app.routers import chat_resume_entries, chat_run_entries
from app.routers.chat_resume_entries import (
    _PROTOCOL_NON_PENDING_TERMINAL_EVENTS,
    _is_pending_interrupt,
    _is_protocol_terminal_event,
    _latest_pending_interrupt_trace,
    _legacy_interrupt_id,
    _log_resume_reject,
)
from app.routers.chat_run_entries import (
    _broker_resume_generator,
    _normalize_event_id,
    _replay_resume_generator,
)
from app.schemas.conversation import MessageCreate, MessagesEnvelope, ResumeRequest
from app.schemas.conversation_run import ConversationRunHistoryResponse
from app.schemas.conversation_scope import ConversationAutoTitle
from app.services import (
    chat_service,
    conversation_run_service,
    thread_branch_service,
    trace_storage,
)
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from app.services.chat import secrets as chat_secrets
from app.services.conversation_audit_service import record_conversation_audit
from app.services.conversation_run_worker import start_conversation_run
from app.services.conversation_stream_service import (
    execute_agent_stream,
    resolve_agent_context,
    resume_agent_stream,
    sse_response,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["conversations"])

__all__ = ["AsyncGenerator", "EventBroker", "HTTPException", "Iterable", "JsonValue", "Mapping"]
__all__ += ["ConversationAutoTitle", "MessageEvent", "OwnedExecutionRequest", "event_names"]
__all__ += ["_PROTOCOL_NON_PENDING_TERMINAL_EVENTS", "_broker_resume_generator", "agent_not_found"]
__all__ += ["_is_pending_interrupt", "_is_protocol_terminal_event", "_legacy_interrupt_id"]
__all__ += ["_latest_pending_interrupt_trace", "_log_resume_reject", "_normalize_event_id"]
__all__ += ["_replay_resume_generator", "accept_owned_execution", "broker_registry", "chat_secrets"]
__all__ += ["conversation_not_found", "execute_agent_stream", "format_sse", "resume_agent_stream"]
__all__ += ["load_private_conversation", "project_and_redact_protocol_data", "resume_not_found"]
__all__ += ["record_conversation_audit", "require_conversation_agent_id", "resolve_agent_context"]
__all__ += ["resume_interrupt_pending", "slice_events_after", "sse_response", "trace_storage"]
__all__ += ["start_conversation_run"]


def _cfg_agent_uuid(cfg: Any):
    if not cfg.agent_id:
        raise agent_not_found()
    return uuid.UUID(cfg.agent_id)


@router.post("/api/agents/{agent_id}/conversations/start")
@authz_policy(PRIVATE("conversations"))
async def start_conversation_with_message(
    agent_id: uuid.UUID,
    data: MessageCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    _authz: AuthzService = Depends(bind_request_authz),
):
    return await chat_run_entries.start_conversation_with_message(
        agent_id=agent_id, data=data, request=request, db=db, user=user, context=context
    )


@router.get(
    "/api/conversations/{conversation_id}/messages",
    response_model=MessagesEnvelope,
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def list_messages(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    conv: Conversation = Depends(owned_conversation),
):
    active_run = await conversation_run_service.current_run_for_conversation(
        db,
        conversation_id=conversation_id,
        context=context,
    )
    active_run_response = (
        ConversationRunHistoryResponse.model_validate(active_run)
        if active_run is not None
        else None
    )
    latest_run = await conversation_run_service.latest_run_for_conversation(
        db,
        conversation_id=conversation_id,
        context=context,
    )
    latest_run_response = (
        ConversationRunHistoryResponse.model_validate(latest_run)
        if latest_run is not None
        else None
    )

    from app.agent_runtime.checkpointer import get_checkpointer
    from app.agent_runtime.conversation_checkpointer import conversation_checkpointer

    tree = None
    try:
        checkpointer = await conversation_checkpointer(
            str(conversation_id), context, db=db, saver=get_checkpointer
        )
        tree = await thread_branch_service.build_message_tree(
            checkpointer,
            str(conversation_id),
            active_checkpoint_id=conv.active_branch_checkpoint_id,
        )
    except RuntimeError:
        tree = None

    messages = []
    if tree is not None or conv.agent_lifecycle_state == "purged":
        messages = await chat_service.list_messages_from_checkpointer(
            db, conv, context=context, user_id=user.id, tree=tree
        )

    total_cost = sum(
        (m.usage.estimated_cost or 0.0)
        for m in messages
        if m.usage and m.usage.estimated_cost is not None
    )

    if tree is None:
        return MessagesEnvelope(
            messages=messages,
            active_run=active_run_response,
            latest_run=latest_run_response,
            total_estimated_cost=total_cost,
        )

    active_tip: uuid.UUID | None = None
    if tree.active_tip_message_id:
        from app.agent_runtime.message_utils import parse_msg_id

        active_tip = parse_msg_id(tree.active_tip_message_id, conversation_id, 0)
    return MessagesEnvelope(
        messages=messages,
        active_run=active_run_response,
        latest_run=latest_run_response,
        active_tip_message_id=active_tip,
        active_checkpoint_id=conv.active_branch_checkpoint_id or tree.active_checkpoint_id,
        total_estimated_cost=total_cost,
    )


@router.get("/api/conversations/{conversation_id}/stream")
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def stream_resume(
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    last_event_id: str | None = Query(None),
    last_event_id_header: str | None = Header(None, alias="Last-Event-ID"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
) -> StreamingResponse:
    return await chat_resume_entries.stream_resume(
        conversation_id=conversation_id,
        run_id=run_id,
        last_event_id=last_event_id,
        last_event_id_header=last_event_id_header,
        db=db,
        user=user,
        context=context,
    )


@router.post("/api/conversations/{conversation_id}/messages")
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def send_message(
    conversation_id: uuid.UUID,
    data: MessageCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    _authz: AuthzService = Depends(bind_request_authz),
):
    return await chat_run_entries.send_message(
        conversation_id=conversation_id,
        data=data,
        request=request,
        db=db,
        user=user,
        context=context,
    )


@router.post("/api/conversations/{conversation_id}/messages/resume")
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def resume_message(
    conversation_id: uuid.UUID,
    data: ResumeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    _authz: AuthzService = Depends(bind_request_authz),
):
    return await chat_resume_entries.resume_message(
        conversation_id=conversation_id,
        data=data,
        request=request,
        db=db,
        user=user,
        context=context,
    )
