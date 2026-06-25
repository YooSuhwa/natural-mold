from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.routers import conversation_agent_protocol_state as state_api
from app.routers.conversation_agent_protocol_contracts import HistoryRequest
from app.routers.conversation_agent_protocol_runtime import get_owned_thread

router = APIRouter(tags=["conversations"])


@router.post("/threads/{thread_id}/history")
async def get_sdk_thread_history(
    thread_id: uuid.UUID,
    request: HistoryRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> list[dict[str, object]]:
    conversation = await get_owned_thread(
        db,
        conversation_id=thread_id,
        thread_id=str(thread_id),
        user_id=user.id,
    )
    return await state_api.load_thread_history_response(conversation, request, db=db, user=user)
