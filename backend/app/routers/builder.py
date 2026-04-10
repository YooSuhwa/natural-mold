"""Builder v2 라우터 — POST /start, GET /stream, GET /{id}, POST /confirm."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db
from app.error_codes import (
    agent_creation_failed,
    no_draft_config,
    session_already_claimed,
    session_confirming,
    session_not_found,
    session_not_preview,
)
from app.exceptions import ValidationError
from app.routers.agents import _agent_to_response
from app.schemas.agent import AgentResponse
from app.schemas.builder import BuilderSessionResponse, BuilderStartRequest, BuilderStatus
from app.services import builder_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/builder", tags=["builder"])


@router.post("", response_model=BuilderSessionResponse, status_code=201)
async def start_build(
    data: BuilderStartRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """빌드 세션을 시작한다."""
    session = await builder_service.create_session(db, user.id, data.user_request)
    return session


@router.get("/{session_id}", response_model=BuilderSessionResponse)
async def get_build_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """빌드 세션 상태를 조회한다."""
    session = await builder_service.get_session(db, session_id, user.id)
    if not session:
        raise session_not_found()
    return session


@router.get("/{session_id}/stream")
async def stream_build(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """빌드 파이프라인을 SSE 스트리밍으로 실행한다."""
    session = await builder_service.get_session(db, session_id, user.id)
    if not session:
        raise session_not_found()

    # 원자적 상태 전환으로 동시 요청 시 중복 실행 방지
    claimed = await builder_service.claim_for_streaming(db, session_id, user.id)
    if not claimed:
        raise session_already_claimed()

    # SSE 스트리밍은 응답 이후에도 실행되므로 Depends(get_db) 세션을 전달하지 않는다.
    # run_build_stream 내부에서 자체 세션을 생성한다.
    return StreamingResponse(
        builder_service.run_build_stream(
            session_id=session.id,
            user_id=session.user_id,
            user_request=session.user_request,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/{session_id}/confirm",
    response_model=AgentResponse,
    status_code=201,
)
async def confirm_build(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """빌드를 확인하고 실제 에이전트를 생성한다.

    멱등성 보장:
    - COMPLETED + agent_id 있음 → 기존 Agent 반환 (중복 생성 방지)
    - CONFIRMING → 409 (다른 요청이 처리 중)
    - PREVIEW → CONFIRMING 원자적 전환 후 Agent 생성
    """
    session = await builder_service.get_session(db, session_id, user.id)
    if not session:
        raise session_not_found()

    # 멱등: 이미 완료된 세션이면 기존 Agent 반환
    if session.status == BuilderStatus.COMPLETED and session.agent_id:
        existing_agent = await builder_service.get_agent_by_id(db, session.agent_id)
        if existing_agent:
            return _agent_to_response(existing_agent)

    # 동시 요청 방지: 이미 CONFIRMING이면 409
    if session.status == BuilderStatus.CONFIRMING:
        raise session_confirming()

    if session.status != BuilderStatus.PREVIEW:
        raise session_not_preview()
    if not session.draft_config:
        raise no_draft_config()

    # 원자적 PREVIEW → CONFIRMING 전환
    claimed = await builder_service.claim_for_confirming(db, session_id, user.id)
    if not claimed:
        raise session_confirming()

    # 세션을 다시 로드 (상태 전환 후 fresh 상태)
    session = await builder_service.get_session(db, session_id, user.id)
    if not session:
        raise session_not_found()

    try:
        agent = await builder_service.confirm_build(db, session)
    except ValueError as exc:
        raise ValidationError("MODEL_NOT_FOUND", str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Unexpected error during confirm_build for session %s",
            session_id,
        )
        raise agent_creation_failed() from exc

    if not agent:
        raise agent_creation_failed("에이전트를 생성할 수 없습니다")
    return _agent_to_response(agent)
