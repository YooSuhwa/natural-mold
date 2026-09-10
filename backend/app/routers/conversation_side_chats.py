from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, owned_conversation, verify_csrf
from app.schemas.conversation import ConversationResponse
from app.services import conversation_side_chat_service
from app.services.conversation_audit_service import record_conversation_audit

router = APIRouter(tags=["conversations"])


@router.post(
    "/api/conversations/{conversation_id}/side-chats",
    response_model=ConversationResponse,
    status_code=201,
)
async def create_side_chat(
    conversation_id: uuid.UUID,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ConversationResponse:
    """Open the parent's independent same-agent side chat, creating it once."""
    parent = await owned_conversation(conversation_id, db, user)
    conversation, created = await conversation_side_chat_service.get_or_create_side_chat(
        db, parent.id
    )
    if not created:
        response.status_code = 200
        await db.commit()
        return ConversationResponse.model_validate(conversation)
    await record_conversation_audit(
        db,
        user=user,
        request=request,
        action="conversation.side_chat_create",
        conversation_id=conversation.id,
        agent_id=parent.agent_id,
        title=conversation.title,
        metadata={"parent_conversation_id": str(parent.id)},
    )
    await db.commit()
    return ConversationResponse.model_validate(conversation)


@router.post(
    "/api/conversations/{conversation_id}/side-chats/save",
    response_model=ConversationResponse,
)
async def save_side_chat(
    conversation_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> ConversationResponse:
    """Idempotently make a side chat discoverable as a regular conversation."""
    conversation = await owned_conversation(conversation_id, db, user)
    if conversation_side_chat_service.promote_side_chat(conversation):
        await record_conversation_audit(
            db,
            user=user,
            request=request,
            action="conversation.side_chat_save",
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            title=conversation.title,
        )
        await db.commit()
    return ConversationResponse.model_validate(conversation)
