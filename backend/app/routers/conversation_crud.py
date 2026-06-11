from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import agent_not_found, conversation_not_found
from app.models.conversation import Conversation
from app.schemas.conversation import (
    ConversationCreate,
    ConversationListEnvelope,
    ConversationResponse,
    ConversationUpdate,
)
from app.schemas.conversation_run import ConversationRunResponse
from app.services import chat_service, conversation_run_service
from app.services.conversation_audit_service import record_conversation_audit

router = APIRouter(tags=["conversations"])


async def _conversation_responses_with_active_runs(
    db: AsyncSession,
    items: list[Conversation],
) -> list[ConversationResponse]:
    active_runs = await conversation_run_service.active_runs_for_conversations(
        db,
        [item.id for item in items],
    )
    responses: list[ConversationResponse] = []
    for item in items:
        active_run = active_runs.get(item.id)
        response = ConversationResponse.model_validate(item)
        responses.append(
            response.model_copy(
                update={
                    "active_run": (
                        ConversationRunResponse.model_validate(active_run)
                        if active_run is not None
                        else None
                    )
                }
            )
        )
    return responses


@router.get(
    "/api/agents/{agent_id}/conversations/page",
    response_model=ConversationListEnvelope,
)
async def list_conversations_page(
    agent_id: uuid.UUID,
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = Query(None),
    q: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    if not await chat_service.is_agent_owned_by_user(db, agent_id, user.id):
        raise agent_not_found()
    try:
        items, next_cursor, has_more = await chat_service.list_conversations_page(
            db,
            agent_id,
            limit=limit,
            cursor=cursor,
            q=q,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc
    return ConversationListEnvelope(
        items=await _conversation_responses_with_active_runs(db, items),
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get(
    "/api/agents/{agent_id}/conversations",
    response_model=list[ConversationResponse],
)
async def list_conversations(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    agent = await chat_service.get_agent_with_tools(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()
    items = await chat_service.list_conversations(db, agent_id)
    return await _conversation_responses_with_active_runs(db, items)


@router.post(
    "/api/agents/{agent_id}/conversations",
    response_model=ConversationResponse,
    status_code=201,
)
async def create_conversation(
    agent_id: uuid.UUID,
    data: ConversationCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    agent = await chat_service.get_agent_with_tools(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()
    conv = await chat_service.create_conversation(db, agent_id, data.title)
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.create",
        conversation_id=conv.id,
        agent_id=agent_id,
        title=conv.title,
    )
    await db.commit()
    return conv


@router.patch(
    "/api/conversations/{conversation_id}",
    response_model=ConversationResponse,
)
async def update_conversation(
    conversation_id: uuid.UUID,
    data: ConversationUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if not conv:
        raise conversation_not_found()
    updated = await chat_service.update_conversation(db, conv, data)
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.update",
        conversation_id=updated.id,
        agent_id=updated.agent_id,
        title=updated.title,
        metadata={"changed_fields": sorted(data.model_fields_set)},
    )
    await db.commit()
    return updated


@router.post(
    "/api/conversations/{conversation_id}/read",
    response_model=ConversationResponse,
)
async def mark_conversation_read(
    conversation_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if not conv:
        raise conversation_not_found()
    updated = await chat_service.mark_conversation_read(db, conv)
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.mark_read",
        conversation_id=updated.id,
        agent_id=updated.agent_id,
        title=updated.title,
    )
    await db.commit()
    return updated


@router.delete("/api/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if not conv:
        raise conversation_not_found()
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.delete",
        conversation_id=conv.id,
        agent_id=conv.agent_id,
        title=conv.title,
    )
    await chat_service.delete_conversation(db, conv)
    await db.commit()
