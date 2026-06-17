from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.trigger_eval import optimize_trigger_description
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import invalid_skill_package
from app.routers.skill_builder_support import (
    get_session_or_404,
    record_builder_audit,
    require_system_llm,
)
from app.schemas.skill_builder import SkillBuilderSessionResponse, SkillDraftPackage
from app.services import skill_builder_service

router = APIRouter(prefix="/api/skill-builder", tags=["skill-builder"])


@router.post("/{session_id}/trigger-eval/run", response_model=SkillBuilderSessionResponse)
async def run_skill_builder_trigger_eval(
    session_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> SkillBuilderSessionResponse:
    session = await get_session_or_404(db, session_id=session_id, user=user)
    await require_system_llm(db, user=user, request=request)
    try:
        draft = SkillDraftPackage.model_validate(session.draft_package)
    except ValidationError as exc:
        raise invalid_skill_package("skill builder draft is required") from exc

    optimized, result = optimize_trigger_description(draft=draft, intent=session.user_request)
    await skill_builder_service.save_trigger_eval_result(
        db,
        session,
        result=result,
        draft=optimized.model_dump(mode="json"),
    )
    await record_builder_audit(
        db,
        user=user,
        request=request,
        action="skill_builder.trigger_eval_complete",
        session_id=session.id,
        mode=session.mode,
        source_skill_id=session.source_skill_id,
        selected_label=(
            str(result["selected"]["label"]) if isinstance(result["selected"], dict) else None
        ),
    )
    await db.commit()
    await db.refresh(session)
    return SkillBuilderSessionResponse.model_validate(session)
