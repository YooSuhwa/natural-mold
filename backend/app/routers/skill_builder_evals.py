from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import invalid_skill_package
from app.routers.skill_builder_support import (
    get_session_or_404,
    record_builder_audit,
    require_system_llm,
)
from app.schemas.skill_builder import JsonValue, SkillBuilderSessionResponse
from app.services import skill_builder_eval_service
from app.services.skill_builder_errors import SkillBuilderValidationError

router = APIRouter(prefix="/api/skill-builder", tags=["skill-builder"])


@router.post("/{session_id}/evals/run", response_model=SkillBuilderSessionResponse)
async def run_skill_builder_evaluation(
    session_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> SkillBuilderSessionResponse:
    session = await get_session_or_404(db, session_id=session_id, user=user)
    await require_system_llm(db, user=user, request=request)
    try:
        result = await skill_builder_eval_service.run_builder_session_evaluation(db, session)
    except SkillBuilderValidationError as exc:
        session.validation_result = exc.result
        await record_builder_audit(
            db,
            user=user,
            request=request,
            action="skill_builder.evaluation_blocked",
            session_id=session.id,
            mode=session.mode,
            source_skill_id=session.source_skill_id,
            outcome="denied",
            error_count=exc.result.get("error_count"),
        )
        await db.commit()
        raise invalid_skill_package("skill builder evaluation could not run") from exc

    await record_builder_audit(
        db,
        user=user,
        request=request,
        action="skill_builder.evaluation_complete",
        session_id=session.id,
        mode=session.mode,
        source_skill_id=session.source_skill_id,
        case_count=_case_count(result),
    )
    await db.commit()
    await db.refresh(session)
    return SkillBuilderSessionResponse.model_validate(session)


def _case_count(result: dict[str, JsonValue]) -> int | None:
    summary = result.get("summary")
    if not isinstance(summary, dict):
        return None
    value = summary.get("case_count")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None
