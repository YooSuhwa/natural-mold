from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_agent_checks import bind_request_authz, require_agent_run
from app.authz.conversation_scope import load_private_conversation, require_private_conversation
from app.authz.org_context import OrgContext
from app.authz.policies import PRIVATE, authz_policy
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.schemas.conversation_refs import ConversationRef, conversation_ref
from app.schemas.conversation_run_input import (
    ConversationRunInputEditRequest,
    ConversationRunInputListResponse,
    ConversationRunInputPromoteRequest,
    ConversationRunInputReorderRequest,
    ConversationRunInputResponse,
)
from app.services import conversation_run_queue_service
from app.services.chat_resource_context_integration import freeze_resource_context_payload
from app.services.chat_resource_context_sources import ResourceContextScope
from app.services.conversation_run_queue_promotion import promote_pending_input
from app.services.conversation_run_queue_worker import dispatch_next_for_conversation

router = APIRouter(tags=["conversation-run-inputs"])


async def _owned_conversation(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    context: OrgContext,
) -> ConversationRef:
    conversation = await load_private_conversation(db, conversation_id, context)
    if conversation is None:
        raise conversation_run_queue_service.queue_input_not_found()
    return conversation_ref(conversation)


async def _executable_conversation(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    context: OrgContext,
) -> ConversationRef:
    agent_id = await conversation_run_queue_service.require_queue_executable(
        db, conversation_id, context
    )
    conversation = await load_private_conversation(db, conversation_id, context)
    if conversation is None or conversation.agent_id != agent_id:
        raise conversation_run_queue_service.queue_input_not_found()
    return conversation_ref(conversation)


@router.get(
    "/api/conversations/{conversation_id}/run-inputs",
    response_model=ConversationRunInputListResponse,
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def list_conversation_run_inputs(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
) -> ConversationRunInputListResponse:
    conversation = await require_private_conversation(db, conversation_id, context)
    items = await conversation_run_queue_service.list_inputs(
        db,
        conversation_id=conversation_id,
        context=context,
    )
    return ConversationRunInputListResponse(
        queue_paused=conversation.queue_paused,
        items=[ConversationRunInputResponse.model_validate(item) for item in items],
    )


@router.patch(
    "/api/conversations/{conversation_id}/run-inputs/{input_id}",
    response_model=ConversationRunInputResponse,
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def edit_conversation_run_input(
    conversation_id: uuid.UUID,
    input_id: uuid.UUID,
    payload: ConversationRunInputEditRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
) -> ConversationRunInputResponse:
    conversation = await _executable_conversation(db, conversation_id, context)
    input_payload = await freeze_resource_context_payload(
        db,
        ResourceContextScope(
            context=context,
            agent_id=conversation.agent_id,
            conversation_id=conversation.id,
        ),
        payload.input,
    )
    item = await conversation_run_queue_service.edit_pending_input(
        db,
        conversation_id=conversation_id,
        input_id=input_id,
        context=context,
        expected_revision=payload.expected_revision,
        input_payload=input_payload,
    )
    await db.commit()
    return ConversationRunInputResponse.model_validate(item)


@router.delete(
    "/api/conversations/{conversation_id}/run-inputs/{input_id}",
    response_model=ConversationRunInputResponse,
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def delete_conversation_run_input(
    conversation_id: uuid.UUID,
    input_id: uuid.UUID,
    expected_revision: int = Query(ge=1),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
) -> ConversationRunInputResponse:
    await _executable_conversation(db, conversation_id, context)
    item = await conversation_run_queue_service.delete_pending_input(
        db,
        conversation_id=conversation_id,
        input_id=input_id,
        context=context,
        expected_revision=expected_revision,
    )
    await db.commit()
    return ConversationRunInputResponse.model_validate(item)


@router.post(
    "/api/conversations/{conversation_id}/run-inputs/{input_id}/promote",
    response_model=ConversationRunInputResponse,
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def promote_conversation_run_input(
    conversation_id: uuid.UUID,
    input_id: uuid.UUID,
    payload: ConversationRunInputPromoteRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    _authz: AuthzService = Depends(bind_request_authz),
) -> ConversationRunInputResponse:
    await _executable_conversation(db, conversation_id, context)
    promoted = await promote_pending_input(
        db,
        conversation_id=conversation_id,
        input_id=input_id,
        context=context,
        expected_revision=payload.expected_revision,
    )
    await db.commit()
    if promoted.predecessor_run_id is not None:
        from app.services.conversation_run_worker import get_run_task_registry

        get_run_task_registry().request_cancel(promoted.predecessor_run_id, reason="steer")
    else:
        await dispatch_next_for_conversation(conversation_id)
    await db.refresh(promoted.input)
    return ConversationRunInputResponse.model_validate(promoted.input)


@router.post(
    "/api/conversations/{conversation_id}/run-inputs/reorder",
    response_model=list[ConversationRunInputResponse],
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def reorder_conversation_run_inputs(
    conversation_id: uuid.UUID,
    payload: ConversationRunInputReorderRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
) -> list[ConversationRunInputResponse]:
    await _executable_conversation(db, conversation_id, context)
    items = await conversation_run_queue_service.reorder_pending_inputs(
        db,
        conversation_id=conversation_id,
        context=context,
        ordered_input_ids=payload.ordered_input_ids,
        expected_revisions=payload.expected_revisions,
    )
    await db.commit()
    return [ConversationRunInputResponse.model_validate(item) for item in items]


@router.post(
    "/api/conversations/{conversation_id}/run-inputs/resume",
    response_model=ConversationRunInputListResponse,
)
@authz_policy(PRIVATE("conversations", param="conversation_id"))
async def resume_conversation_run_inputs(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(bind_request_authz),
) -> ConversationRunInputListResponse:
    executable = await _executable_conversation(db, conversation_id, context)
    await require_agent_run(context, executable.agent_id, authz=authz)
    await conversation_run_queue_service.resume_queue(
        db,
        conversation_id=conversation_id,
        context=context,
    )
    await db.commit()
    await dispatch_next_for_conversation(conversation_id)
    conversation = await _owned_conversation(db, conversation_id, context)
    items = await conversation_run_queue_service.list_inputs(
        db,
        conversation_id=conversation_id,
        context=context,
    )
    return ConversationRunInputListResponse(
        queue_paused=conversation.queue_paused,
        items=[ConversationRunInputResponse.model_validate(item) for item in items],
    )
