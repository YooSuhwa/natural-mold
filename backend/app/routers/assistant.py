"""Assistant v2 라우터 — POST /api/agents/{agent_id}/assistant/message."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db
from app.error_codes import agent_not_found
from app.schemas.assistant import AssistantMessageRequest
from app.services import agent_service, assistant_service

router = APIRouter(prefix="/api/agents", tags=["assistant"])


@router.post("/{agent_id}/assistant/message")
async def send_assistant_message(
    agent_id: uuid.UUID,
    data: AssistantMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Assistant에게 메시지를 보내고 SSE 스트리밍으로 응답받는다."""
    agent = await agent_service.get_agent(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()

    # thread_id: 프론트엔드가 session_id를 제공하면 그것을 사용 (탭별 독립 대화)
    # 미제공 시 agent_id 기반 기본값으로 폴백 (하위 호환)
    if data.session_id:
        thread_id = f"assistant_{agent_id}_{data.session_id}"
    else:
        thread_id = f"assistant_{agent_id}"

    return StreamingResponse(
        assistant_service.stream_assistant_message(
            db=db,
            agent_id=agent_id,
            user_id=user.id,
            thread_id=thread_id,
            user_message=data.content,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
