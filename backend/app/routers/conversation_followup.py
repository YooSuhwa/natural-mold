"""Follow-up suggestion endpoint (composer ghost text).

POST-per-run (프론트의 런 종료 훅이 1회 호출) — 폴링 경로가 아니므로
checkpointer tail 조회 비용은 run당 1회로 제한된다.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, owned_conversation, verify_csrf
from app.models.conversation import Conversation
from app.services.followup_service import generate_followup_suggestion

router = APIRouter(tags=["conversations"])


class FollowupSuggestionResponse(BaseModel):
    suggestion: str | None


@router.post(
    "/api/conversations/{conversation_id}/followup-suggestion",
    response_model=FollowupSuggestionResponse,
)
async def create_followup_suggestion(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
    conversation: Conversation = Depends(owned_conversation),
) -> FollowupSuggestionResponse:
    suggestion = await generate_followup_suggestion(db, conversation, user.id)
    return FollowupSuggestionResponse(suggestion=suggestion)
