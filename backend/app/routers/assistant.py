"""Assistant v2 라우터 — POST /api/agents/{agent_id}/assistant/message."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import RES, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.schemas.assistant import AssistantMessageRequest, AssistantResumeRequest
from app.services import agent_access_views, assistant_service

router = APIRouter(prefix="/api/agents", tags=["assistant"])


def _assistant_thread_id(agent_id: uuid.UUID, session_id: str | None) -> str:
    if session_id:
        return f"assistant_{agent_id}_{session_id}"
    return f"assistant_{agent_id}"


@router.post("/{agent_id}/assistant/message")
@authz_policy(RES("agent", "can_edit", param="agent_id", min_visible="can_run"))
async def send_assistant_message(
    agent_id: uuid.UUID,
    data: AssistantMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
):
    """Assistant에게 메시지를 보내고 SSE 스트리밍으로 응답받는다."""
    # Assistant tools act with the caller's permissions and re-check per call (11.7).
    await agent_access_views.authorized_agent(db, authz, context, agent_id, "can_edit")

    thread_id = _assistant_thread_id(agent_id, data.session_id)

    return StreamingResponse(
        assistant_service.stream_assistant_message(
            db=db,
            agent_id=agent_id,
            context=context,
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


@router.post("/{agent_id}/assistant/message/resume")
@authz_policy(RES("agent", "can_edit", param="agent_id", min_visible="can_run"))
async def resume_assistant_message(
    agent_id: uuid.UUID,
    data: AssistantResumeRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
):
    # Assistant tools act with the caller's permissions and re-check per call (11.7).
    await agent_access_views.authorized_agent(db, authz, context, agent_id, "can_edit")

    thread_id = _assistant_thread_id(agent_id, data.session_id)

    return StreamingResponse(
        assistant_service.stream_assistant_resume(
            db=db,
            agent_id=agent_id,
            context=context,
            thread_id=thread_id,
            decisions=data.decisions,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
