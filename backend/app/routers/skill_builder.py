from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import (
    invalid_skill_package,
    session_confirming,
    skill_builder_session_not_ready,
    skill_builder_source_conflict,
    skill_not_found,
    system_llm_not_configured,
)
from app.routers.skill_builder_audit import (
    confirm_audit_metadata,
    record_current_revision_create_audit,
)
from app.routers.skill_builder_support import (
    completed_skill,
    get_session_or_404,
    record_builder_audit,
    record_secret_scan_blocked_if_needed,
    require_system_llm,
)
from app.schemas.skill import SkillResponse
from app.schemas.skill_builder import (
    SkillBuilderMessageRequest,
    SkillBuilderMode,
    SkillBuilderSessionResponse,
    SkillBuilderStartRequest,
    SkillBuilderStatus,
    SkillDraftPackage,
)
from app.services import (
    chat_service,
    skill_builder_service,
    skill_builder_workflow,
    skill_draft_workspace,
)
from app.services.skill_builder_errors import (
    SkillBuilderConflictError,
    SkillBuilderSourceSkillNotFound,
    SkillBuilderValidationError,
)
from app.services.skill_builder_hidden_agent import get_or_create_skill_builder_agent
from app.services.system_credential_resolver import SystemModelNotConfiguredError
from app.skills.validator import validate_draft_package

router = APIRouter(prefix="/api/skill-builder", tags=["skill-builder"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _session_response(
    session: object, *, agent_id: uuid.UUID | None
) -> SkillBuilderSessionResponse:
    response = SkillBuilderSessionResponse.model_validate(session)
    if agent_id is not None:
        # ``agent_id``는 ORM 속성이 아니라 대화 역참조 파생값 — 검증 후 주입.
        response = response.model_copy(update={"agent_id": agent_id})
    return response


@router.post("", response_model=SkillBuilderSessionResponse, status_code=201)
async def start_skill_builder(
    data: SkillBuilderStartRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> SkillBuilderSessionResponse:
    """빌더 챗 세션 시작 (v2, 스펙 AD-6).

    히든 빌더 에이전트 lazy-seed → 세션 row → 드래프트 워크스페이스 →
    draft conversation을 만들고 ``{session, agent_id, conversation_id}``를
    반환한다. 프론트는 이 값으로 ``/skills/builder/[sessionId]``에 진입한다.
    """

    await require_system_llm(db, user=user, request=request)
    try:
        session = await skill_builder_service.create_session(
            db,
            user_id=user.id,
            user_request=data.user_request,
            mode=data.mode,
            source_skill_id=data.source_skill_id,
        )
    except SkillBuilderSourceSkillNotFound as exc:
        raise skill_not_found() from exc
    try:
        agent = await get_or_create_skill_builder_agent(db, user.id)
    except SystemModelNotConfiguredError as exc:
        # 모델 카탈로그가 비어 seed용 FK를 채울 수 없는 경우 — 게이트와 동일 계약.
        raise system_llm_not_configured() from exc
    # source="draft" — 첫 메시지 전송 시 promote되는 기존 draft 계약 재사용.
    # 네비게이터 노출은 runtime_profile 필터가 promote 이후에도 차단한다.
    conversation = await chat_service.create_conversation(
        db, agent.id, source="draft"
    )
    workspace_path = skill_draft_workspace.create_workspace(session.id)
    await skill_builder_service.attach_chat_runtime(
        db,
        session,
        conversation_id=conversation.id,
        draft_workspace_path=workspace_path,
    )
    await record_builder_audit(
        db,
        user=user,
        request=request,
        action="skill_builder.session_create",
        session_id=session.id,
        mode=data.mode.value,
        source_skill_id=data.source_skill_id,
        conversation_id=str(conversation.id),
        agent_id=str(agent.id),
    )
    await db.commit()
    await db.refresh(session)
    return _session_response(session, agent_id=agent.id)


@router.get("/{session_id}", response_model=SkillBuilderSessionResponse)
async def get_skill_builder_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> SkillBuilderSessionResponse:
    session = await get_session_or_404(db, session_id=session_id, user=user)
    agent_id = await skill_builder_service.resolve_session_agent_id(db, session)
    return _session_response(session, agent_id=agent_id)


@router.post("/{session_id}/messages")
async def post_skill_builder_message(
    session_id: uuid.UUID,
    payload: SkillBuilderMessageRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> StreamingResponse:
    await require_system_llm(db, user=user, request=request)
    session = await get_session_or_404(db, session_id=session_id, user=user)
    events = await skill_builder_workflow.run_skill_builder_message_workflow(
        db,
        session=session,
        user_id=user.id,
        content=payload.content,
    )
    return StreamingResponse(
        skill_builder_workflow.stream_skill_builder_events(events),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/{session_id}/messages/resume")
async def resume_skill_builder_message(
    session_id: uuid.UUID,
    payload: SkillBuilderMessageRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> StreamingResponse:
    await require_system_llm(db, user=user, request=request)
    session = await get_session_or_404(db, session_id=session_id, user=user)
    events = await skill_builder_workflow.run_skill_builder_message_workflow(
        db,
        session=session,
        user_id=user.id,
        content=payload.content,
    )
    return StreamingResponse(
        skill_builder_workflow.stream_skill_builder_events(events),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/{session_id}/validate", response_model=SkillBuilderSessionResponse)
async def validate_skill_builder_draft(
    session_id: uuid.UUID,
    draft: SkillDraftPackage,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> SkillBuilderSessionResponse:
    session = await get_session_or_404(db, session_id=session_id, user=user)
    await skill_builder_service.save_draft_package(
        db,
        session,
        draft=draft.model_dump(mode="json"),
    )
    result = validate_draft_package(
        files=draft.files,
        credential_requirements=draft.credential_requirements,
        execution_profile=draft.execution_profile,
    )
    await skill_builder_service.save_validation_result(db, session, result=result)
    if result["error_count"] > 0:
        await record_builder_audit(
            db,
            user=user,
            request=request,
            action="skill_builder.validation_failed",
            session_id=session.id,
            mode=session.mode,
            error_count=result["error_count"],
        )
        await record_secret_scan_blocked_if_needed(
            db,
            user=user,
            request=request,
            session=session,
            validation_result=result,
        )
    await db.commit()
    await db.refresh(session)
    return SkillBuilderSessionResponse.model_validate(session)


@router.post("/{session_id}/confirm", response_model=SkillResponse, status_code=201)
async def confirm_skill_builder_session(
    session_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> SkillResponse:
    session = await get_session_or_404(db, session_id=session_id, user=user)
    existing = await completed_skill(db, session=session, user=user)
    if existing is not None:
        return SkillResponse.model_validate(existing)
    if session.status == SkillBuilderStatus.CONFIRMING.value:
        raise session_confirming()
    if session.status != SkillBuilderStatus.REVIEW.value:
        raise skill_builder_session_not_ready()

    claimed = await skill_builder_service.claim_for_confirming(db, session.id, user.id)
    if not claimed:
        raise session_confirming()
    session = await get_session_or_404(db, session_id=session_id, user=user)
    try:
        skill = await skill_builder_service.confirm_session(db, session, user_id=user.id)
    except SkillBuilderConflictError as exc:
        await record_builder_audit(
            db,
            user=user,
            request=request,
            action="skill_builder.apply_conflict",
            session_id=session.id,
            mode=session.mode,
            source_skill_id=session.source_skill_id,
            old_hash=exc.base_content_hash,
            new_hash=exc.current_content_hash,
            outcome="denied",
        )
        await db.commit()
        raise skill_builder_source_conflict() from exc
    except SkillBuilderSourceSkillNotFound as exc:
        await db.rollback()
        raise skill_not_found() from exc
    except SkillBuilderValidationError as exc:
        await record_secret_scan_blocked_if_needed(
            db,
            user=user,
            request=request,
            session=session,
            validation_result=exc.result,
        )
        await db.commit()
        raise invalid_skill_package("skill builder draft validation failed") from exc

    await record_builder_audit(
        db,
        user=user,
        request=request,
        action=_confirm_action(session.mode),
        session_id=session.id,
        mode=session.mode,
        source_skill_id=session.source_skill_id,
        **confirm_audit_metadata(session, skill),
    )
    await record_current_revision_create_audit(
        db,
        user=user,
        request=request,
        skill=skill,
    )
    await db.commit()
    await db.refresh(skill)
    return SkillResponse.model_validate(skill)


def _confirm_action(mode: str) -> str:
    if mode == SkillBuilderMode.IMPROVE.value:
        return "skill_builder.apply_improvement"
    return "skill_builder.confirm_create"
