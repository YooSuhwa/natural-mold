from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db
from app.exceptions import AppError, NotFoundError, ValidationError
from app.schemas.agent import AgentResponse
from app.schemas.agent_creation import (
    CreationMessageRequest,
    CreationMessageResponse,
    CreationSessionResponse,
)
from app.services import agent_creation_service

router = APIRouter(prefix="/api/agents/create-session", tags=["agent-creation"])


@router.post("", response_model=CreationSessionResponse, status_code=201)
async def start_creation_session(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await agent_creation_service.create_session(db, user.id)


@router.get("/{session_id}", response_model=CreationSessionResponse)
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    session = await agent_creation_service.get_session(db, session_id, user.id)
    if not session:
        raise NotFoundError("SESSION_NOT_FOUND", "세션을 찾을 수 없습니다")
    return session


@router.post("/{session_id}/message", response_model=CreationMessageResponse)
async def send_creation_message(
    session_id: uuid.UUID,
    data: CreationMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    session = await agent_creation_service.get_session(db, session_id, user.id)
    if not session:
        raise NotFoundError("SESSION_NOT_FOUND", "세션을 찾을 수 없습니다")
    if session.status != "in_progress":
        raise ValidationError("SESSION_NOT_IN_PROGRESS", "세션이 진행 중이 아닙니다")

    return await agent_creation_service.send_message(db, session, data.content)


@router.post("/{session_id}/confirm", response_model=AgentResponse, status_code=201)
async def confirm_creation(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    session = await agent_creation_service.get_session(db, session_id, user.id)
    if not session:
        raise NotFoundError("SESSION_NOT_FOUND", "세션을 찾을 수 없습니다")
    if not session.draft_config:
        raise ValidationError("NO_DRAFT_CONFIG", "드래프트 설정이 없습니다")

    agent = await agent_creation_service.confirm_creation(db, session)
    if not agent:
        raise AppError(
            "AGENT_CREATION_FAILED", "설정으로 에이전트를 생성할 수 없습니다", status=500
        )
    return agent
