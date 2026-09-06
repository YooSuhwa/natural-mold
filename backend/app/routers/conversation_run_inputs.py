from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.models.conversation import Conversation
from app.schemas.conversation_run_input import (
    ConversationRunInputEditRequest,
    ConversationRunInputListResponse,
    ConversationRunInputPromoteRequest,
    ConversationRunInputReorderRequest,
    ConversationRunInputResponse,
)
from app.services import chat_service, conversation_run_queue_service
from app.services.conversation_run_queue_promotion import promote_pending_input
from app.services.conversation_run_queue_worker import dispatch_next_for_conversation

router = APIRouter(tags=["conversation-run-inputs"])


async def _owned_conversation(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Conversation:
    conversation = await chat_service.get_owned_conversation(db, conversation_id, user_id)
    if conversation is None:
        raise conversation_run_queue_service.queue_input_not_found()
    return conversation


@router.get(
    "/api/conversations/{conversation_id}/run-inputs",
    response_model=ConversationRunInputListResponse,
)
async def list_conversation_run_inputs(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> ConversationRunInputListResponse:
    conversation = await _owned_conversation(db, conversation_id, user.id)
    items = await conversation_run_queue_service.list_inputs(
        db,
        conversation_id=conversation_id,
        user_id=user.id,
    )
    return ConversationRunInputListResponse(
        queue_paused=conversation.queue_paused,
        items=[ConversationRunInputResponse.model_validate(item) for item in items],
    )


@router.patch(
    "/api/conversations/{conversation_id}/run-inputs/{input_id}",
    response_model=ConversationRunInputResponse,
)
async def edit_conversation_run_input(
    conversation_id: uuid.UUID,
    input_id: uuid.UUID,
    payload: ConversationRunInputEditRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ConversationRunInputResponse:
    item = await conversation_run_queue_service.edit_pending_input(
        db,
        conversation_id=conversation_id,
        input_id=input_id,
        user_id=user.id,
        expected_revision=payload.expected_revision,
        input_payload=payload.input,
    )
    await db.commit()
    return ConversationRunInputResponse.model_validate(item)


@router.delete(
    "/api/conversations/{conversation_id}/run-inputs/{input_id}",
    response_model=ConversationRunInputResponse,
)
async def delete_conversation_run_input(
    conversation_id: uuid.UUID,
    input_id: uuid.UUID,
    expected_revision: int = Query(ge=1),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ConversationRunInputResponse:
    item = await conversation_run_queue_service.delete_pending_input(
        db,
        conversation_id=conversation_id,
        input_id=input_id,
        user_id=user.id,
        expected_revision=expected_revision,
    )
    await db.commit()
    return ConversationRunInputResponse.model_validate(item)


@router.post(
    "/api/conversations/{conversation_id}/run-inputs/{input_id}/promote",
    response_model=ConversationRunInputResponse,
)
async def promote_conversation_run_input(
    conversation_id: uuid.UUID,
    input_id: uuid.UUID,
    payload: ConversationRunInputPromoteRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ConversationRunInputResponse:
    promoted = await promote_pending_input(
        db,
        conversation_id=conversation_id,
        input_id=input_id,
        user_id=user.id,
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
async def reorder_conversation_run_inputs(
    conversation_id: uuid.UUID,
    payload: ConversationRunInputReorderRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> list[ConversationRunInputResponse]:
    items = await conversation_run_queue_service.reorder_pending_inputs(
        db,
        conversation_id=conversation_id,
        user_id=user.id,
        ordered_input_ids=payload.ordered_input_ids,
        expected_revisions=payload.expected_revisions,
    )
    await db.commit()
    return [ConversationRunInputResponse.model_validate(item) for item in items]


@router.post(
    "/api/conversations/{conversation_id}/run-inputs/resume",
    response_model=ConversationRunInputListResponse,
)
async def resume_conversation_run_inputs(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ConversationRunInputListResponse:
    await conversation_run_queue_service.resume_queue(
        db,
        conversation_id=conversation_id,
        user_id=user.id,
    )
    await db.commit()
    await dispatch_next_for_conversation(conversation_id)
    conversation = await _owned_conversation(db, conversation_id, user.id)
    items = await conversation_run_queue_service.list_inputs(
        db,
        conversation_id=conversation_id,
        user_id=user.id,
    )
    return ConversationRunInputListResponse(
        queue_paused=conversation.queue_paused,
        items=[ConversationRunInputResponse.model_validate(item) for item in items],
    )
