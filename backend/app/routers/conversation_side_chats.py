from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, owned_conversation, verify_csrf
from app.models.conversation import Conversation
from app.schemas.conversation import ConversationResponse
from app.services import chat_service
from app.services.conversation_audit_service import record_conversation_audit
from app.services.conversation_runtime_policy import ensure_conversation_runtime_policy

router = APIRouter(tags=["conversations"])


@router.post(
    "/api/conversations/{conversation_id}/side-chats",
    response_model=ConversationResponse,
    status_code=201,
)
async def create_side_chat(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    parent: Conversation = Depends(owned_conversation),
    _csrf: None = Depends(verify_csrf),
) -> Conversation:
    """Open the parent's independent same-agent side chat, creating it once."""
    parent, _, _ = await ensure_conversation_runtime_policy(db, parent.id)
    existing = await db.scalar(
        select(Conversation).where(
            Conversation.side_chat_parent_id == parent.id,
            Conversation.agent_id == parent.agent_id,
        )
    )
    if existing is not None:
        response.status_code = 200
        await db.commit()
        return existing
    conversation = await chat_service.create_conversation(
        db,
        parent.agent_id,
        parent.title,
        source="side_chat",
    )
    conversation.runtime_policy_snapshot = parent.runtime_policy_snapshot
    conversation.side_chat_parent_id = parent.id
    conversation.runtime_policy_version = parent.runtime_policy_version
    conversation.runtime_policy_hash = parent.runtime_policy_hash
    conversation.runtime_policy_source = parent.runtime_policy_source
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
    return conversation


@router.post(
    "/api/conversations/{conversation_id}/side-chats/save",
    response_model=ConversationResponse,
)
async def save_side_chat(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    conversation: Conversation = Depends(owned_conversation),
    _csrf: None = Depends(verify_csrf),
) -> Conversation:
    """Idempotently make a side chat discoverable as a regular conversation."""
    if conversation.source == "side_chat":
        conversation.source = "ui"
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
    return conversation
