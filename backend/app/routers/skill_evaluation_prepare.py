from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.policies import RES, authz_policy
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.authz.skill_access import SKILL_MIN_VISIBLE, authorize_skill
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.routers.skill_evaluation_prepare_support import (
    preparation_response,
    record_preparation_audit,
)
from app.schemas.skill_evaluation import (
    SkillEvaluationPrepareRequest,
    SkillEvaluationPrepareResponse,
)
from app.services.skill_evaluation_set_preparation import prepare_skill_evaluation_set

router = APIRouter(prefix="/api/skills/{skill_id}/evaluations", tags=["skill-evaluations"])


@router.post("/prepare", response_model=SkillEvaluationPrepareResponse)
@authz_policy(RES("skill", "can_edit", param="skill_id", min_visible=SKILL_MIN_VISIBLE))
async def prepare_skill_evaluation(
    skill_id: uuid.UUID,
    data: SkillEvaluationPrepareRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
) -> SkillEvaluationPrepareResponse:
    skill = await authorize_skill(db, authz, context, skill_id, "can_edit")
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
