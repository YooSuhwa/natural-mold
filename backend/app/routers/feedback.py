"""Message feedback router — thumbs up/down per (user, message).

Toggle semantics:
- POST /api/messages/{message_id}/feedback → upsert (replace existing rating).
- DELETE /api/messages/{message_id}/feedback → clear current user's rating.

Messages are owned by the LangGraph checkpointer, so ``message_id`` is just
a string identifier carried in the URL — no FK against a messages table (there
isn't one). Upsert checks (D-10) that the body's conversation is the caller's
(``CONVERSATION_NOT_FOUND``) and that the message is in its checkpoint
(``MESSAGE_NOT_FOUND``).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_scope import load_private_conversation
from app.authz.org_context import OrgContext
from app.authz.policies import PRIVATE, authz_policy
from app.dependencies import (
    CurrentUser,
    get_company_db,
    get_current_user,
    get_db,
    get_org_context,
    verify_csrf,
)
from app.error_codes import conversation_not_found, message_not_found
from app.models.message_feedback import MessageFeedback
from app.schemas.feedback import MessageFeedbackCreate, MessageFeedbackResponse
from app.services.chat.message_membership import conversation_has_message

logger = logging.getLogger(__name__)

router = APIRouter(tags=["feedback"])


@router.post(
    "/api/messages/{message_id}/feedback",
    response_model=MessageFeedbackResponse,
)
@authz_policy(PRIVATE("message_feedbacks"))
async def upsert_feedback(
    message_id: str,
    data: MessageFeedbackCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    context: OrgContext = Depends(get_org_context),
) -> MessageFeedback:
    """Create or replace the current user's rating for ``message_id``."""

    # ``owned_conversation`` reads a path ``conversation_id``; here it is in the body,
    # so the same private loader and 404 run inline (after CSRF).
    conversation = await load_private_conversation(db, data.conversation_id, context)
    if conversation is None:
        raise conversation_not_found()
    if not await conversation_has_message(db, conversation, message_id, context):
        raise message_not_found()

    result = await db.execute(
        select(MessageFeedback).where(
            MessageFeedback.user_id == user.id,
            MessageFeedback.message_id == message_id,
        )
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        existing.rating = data.rating
        existing.comment = data.comment
        existing.conversation_id = data.conversation_id
        await db.commit()
        await db.refresh(existing)
        return existing

    row = MessageFeedback(
        user_id=user.id,
        message_id=message_id,
        conversation_id=data.conversation_id,
        rating=data.rating,
        comment=data.comment,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete(
    "/api/messages/{message_id}/feedback",
    status_code=204,
)
@authz_policy(PRIVATE("message_feedbacks"))
async def clear_feedback(
    message_id: str,
    _csrf: None = Depends(verify_csrf),
    db: AsyncSession = Depends(get_company_db),
    user: CurrentUser = Depends(get_current_user),
) -> None:
    """Remove the current user's rating for ``message_id`` (toggle off)."""

    await db.execute(
        delete(MessageFeedback).where(
            MessageFeedback.user_id == user.id,
            MessageFeedback.message_id == message_id,
        )
    )
    await db.commit()


@router.get(
    "/api/conversations/{conversation_id}/feedback",
    response_model=list[MessageFeedbackResponse],
)
@authz_policy(PRIVATE("message_feedbacks"))
async def list_feedback_for_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_company_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[MessageFeedback]:
    """Return all of the current user's ratings inside a conversation.

    Used by the frontend to hydrate the active rating state when re-opening
    a conversation, so the up/down buttons start in the correct mode.
    """

    result = await db.execute(
        select(MessageFeedback).where(
            MessageFeedback.user_id == user.id,
            MessageFeedback.conversation_id == conversation_id,
        )
    )
    return list(result.scalars().all())
