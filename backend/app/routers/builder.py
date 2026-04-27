"""Builder 라우터 — v2 (legacy GET /stream) + v3 (POST /messages, /messages/resume)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
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


class BuilderMessageRequest(BaseModel):
    """Builder v3 — 메시지 전송 요청."""

    content: str = Field(..., min_length=1, max_length=4000)


class BuilderResumeRequest(BaseModel):
    """Builder v3 — interrupt 응답 요청.

    response 형식 (interrupt type별):
    - ask_user: str (옵션 라벨 또는 자유 텍스트)
    - approval: {"approved": bool, "revision_message": str?}
    - image_choice: {"choice": "skip" | "generate", "prompt": str?}
    - image_approval: {"choice": "confirm" | "regenerate" | "skip", "prompt": str?}

    악의적 페이로드 방어를 위해 dict는 깊이/크기를 제한.
    """

    model_config = {"extra": "forbid"}

    response: dict[str, Any] | str = Field(..., description="interrupt 응답")
    display_text: str | None = Field(None, max_length=200)
    # SSE interrupt 이벤트의 interrupt_id (stale 카드로 응답 시 차단용)
    interrupt_id: str | None = Field(None, max_length=200)

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


# ---------------------------------------------------------------------------
# Builder v3 — 채팅 UI 통합 엔드포인트
# ---------------------------------------------------------------------------

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.post("/{session_id}/messages")
async def post_message(
    session_id: uuid.UUID,
    payload: BuilderMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Builder v3 — 사용자 메시지를 전송하고 SSE 스트림을 받는다.

    첫 메시지: 그래프를 처음부터 실행 (Phase 1부터).
    후속 메시지: 그래프가 진행 중이면 messages만 추가.
    """
    session = await builder_service.get_session(db, session_id, user.id)
    if not session:
        raise session_not_found()

    return StreamingResponse(
        builder_service.run_v3_message_stream(
            session_id=session.id,
            user_id=session.user_id,
            content=payload.content,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/{session_id}/messages/resume")
async def resume_message(
    session_id: uuid.UUID,
    payload: BuilderResumeRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Builder v3 — interrupt 응답을 전달하고 그래프를 재개한다."""
    session = await builder_service.get_session(db, session_id, user.id)
    if not session:
        raise session_not_found()

    return StreamingResponse(
        builder_service.run_v3_resume_stream(
            session_id=session.id,
            user_id=session.user_id,
            response=payload.response,
            interrupt_id=payload.interrupt_id,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get("/{session_id}/image/{filename}")
async def serve_builder_image(
    session_id: uuid.UUID,
    filename: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Phase 6에서 생성한 임시 이미지 미리보기를 서빙한다.

    세션 소유자만 접근 가능 (다른 사용자의 builder 이미지 접근 차단).
    """
    from app.agent_runtime.builder_v3.image_gen import resolve_local_path

    session = await builder_service.get_session(db, session_id, user.id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    path = resolve_local_path(str(session_id), filename)
    if not path:
        raise HTTPException(status_code=404, detail="Image not found")

    return FileResponse(path)
