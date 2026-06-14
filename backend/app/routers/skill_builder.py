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
)
from app.routers.skill_builder_support import (
    completed_skill,
    get_session_or_404,
    record_builder_audit,
    require_system_llm,
    single_event_stream,
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
from app.services import skill_builder_service
from app.services.skill_builder_errors import (
    SkillBuilderConflictError,
    SkillBuilderSourceSkillNotFound,
    SkillBuilderValidationError,
)
from app.skills.validator import validate_draft_package

router = APIRouter(prefix="/api/skill-builder", tags=["skill-builder"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.post("", response_model=SkillBuilderSessionResponse, status_code=201)
async def start_skill_builder(
    data: SkillBuilderStartRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> SkillBuilderSessionResponse:
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
    await record_builder_audit(
        db,
        user=user,
        request=request,
        action="skill_builder.session_create",
        session_id=session.id,
        mode=data.mode.value,
        source_skill_id=data.source_skill_id,
    )
    await db.commit()
    await db.refresh(session)
    return SkillBuilderSessionResponse.model_validate(session)


@router.get("/{session_id}", response_model=SkillBuilderSessionResponse)
async def get_skill_builder_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> SkillBuilderSessionResponse:
    session = await get_session_or_404(db, session_id=session_id, user=user)
    return SkillBuilderSessionResponse.model_validate(session)


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
    await skill_builder_service.append_message(db, session, role="user", content=payload.content)
    await db.commit()
    return StreamingResponse(
        single_event_stream("builder_status", {"status": "queued", "session_id": str(session.id)}),
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
    await skill_builder_service.append_message(db, session, role="user", content=payload.content)
    await db.commit()
    return StreamingResponse(
        single_event_stream(
            "builder_status",
            {"status": "resumed", "session_id": str(session.id)},
        ),
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
        file_count=len((skill.package_metadata or {}).get("files") or []),
        credential_requirement_count=len(skill.credential_requirements or []),
        new_hash=skill.content_hash,
    )
    await db.commit()
    await db.refresh(skill)
    return SkillResponse.model_validate(skill)


def _confirm_action(mode: str) -> str:
    if mode == SkillBuilderMode.IMPROVE.value:
        return "skill_builder.apply_improvement"
    return "skill_builder.confirm_create"
