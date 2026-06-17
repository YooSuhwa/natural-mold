from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.routers.skill_evaluation_prepare_support import (
    preparation_response,
    record_preparation_audit,
)
from app.routers.skill_evaluations_support import load_skill_or_404
from app.schemas.skill_evaluation import (
    SkillEvaluationPrepareRequest,
    SkillEvaluationPrepareResponse,
)
from app.services.skill_evaluation_set_preparation import prepare_skill_evaluation_set

router = APIRouter(prefix="/api/skills/{skill_id}/evaluations", tags=["skill-evaluations"])


@router.post("/prepare", response_model=SkillEvaluationPrepareResponse)
async def prepare_skill_evaluation(
    skill_id: uuid.UUID,
    data: SkillEvaluationPrepareRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> SkillEvaluationPrepareResponse:
    skill = await load_skill_or_404(db, skill_id=skill_id, user=user)
    result = await prepare_skill_evaluation_set(
        db=db,
        skill=skill,
        user_id=user.id,
        source_kind="manual_prepare",
        allow_llm_generation=data.allow_llm_generation,
        force=data.force,
    )
    await record_preparation_audit(
        db,
        user=user,
        request=request,
        skill_id=skill.id,
        result=result,
    )
    await db.commit()
    return preparation_response(result)
