from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import (
    skill_evaluation_run_not_cancellable,
    skill_evaluation_run_not_found,
    skill_evaluation_set_not_found,
    skill_not_found,
)
from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationSet
from app.schemas.skill_evaluation import (
    SkillEvaluationRunCancelRequest,
    SkillEvaluationRunEstimate,
    SkillEvaluationRunResponse,
    SkillEvaluationSetCreate,
    SkillEvaluationSetResponse,
)
from app.services import audit_service, skill_evaluation_service
from app.skills import service as skill_service

router = APIRouter(prefix="/api/skills/{skill_id}/evaluations", tags=["skill-evaluations"])


@router.get("", response_model=list[SkillEvaluationSetResponse])
async def list_skill_evaluations(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[SkillEvaluationSetResponse]:
    skill = await _load_skill_or_404(db, skill_id=skill_id, user=user)
    rows = await skill_evaluation_service.list_evaluation_sets(db, skill=skill, user_id=user.id)
    return [SkillEvaluationSetResponse.model_validate(row) for row in rows]


@router.post("", response_model=SkillEvaluationSetResponse, status_code=201)
async def create_skill_evaluation(
    skill_id: uuid.UUID,
    data: SkillEvaluationSetCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> SkillEvaluationSetResponse:
    skill = await _load_skill_or_404(db, skill_id=skill_id, user=user)
    row = await skill_evaluation_service.create_evaluation_set(
        db,
        user_id=user.id,
        skill=skill,
        name=data.name,
        description=data.description,
        evals=data.evals,
        source_kind="manual",
    )
    await db.commit()
    await db.refresh(row)
    return SkillEvaluationSetResponse.model_validate(row)


@router.get("/{evaluation_set_id}", response_model=SkillEvaluationSetResponse)
async def get_skill_evaluation(
    skill_id: uuid.UUID,
    evaluation_set_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> SkillEvaluationSetResponse:
    skill = await _load_skill_or_404(db, skill_id=skill_id, user=user)
    row = await _load_evaluation_set_or_404(
        db,
        skill=skill,
        user=user,
        evaluation_set_id=evaluation_set_id,
    )
    return SkillEvaluationSetResponse.model_validate(row)


@router.post("/{evaluation_set_id}/estimate", response_model=SkillEvaluationRunEstimate)
async def estimate_skill_evaluation_run(
    skill_id: uuid.UUID,
    evaluation_set_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> SkillEvaluationRunEstimate:
    skill = await _load_skill_or_404(db, skill_id=skill_id, user=user)
    evaluation_set = await _load_evaluation_set_or_404(
        db,
        skill=skill,
        user=user,
        evaluation_set_id=evaluation_set_id,
    )
    return skill_evaluation_service.estimate_run(evaluation_set)


@router.get("/{evaluation_set_id}/runs", response_model=list[SkillEvaluationRunResponse])
async def list_skill_evaluation_runs(
    skill_id: uuid.UUID,
    evaluation_set_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[SkillEvaluationRunResponse]:
    skill = await _load_skill_or_404(db, skill_id=skill_id, user=user)
    evaluation_set = await _load_evaluation_set_or_404(
        db,
        skill=skill,
        user=user,
        evaluation_set_id=evaluation_set_id,
    )
    runs = await skill_evaluation_service.list_runs(
        db,
        skill=skill,
        user_id=user.id,
        evaluation_set=evaluation_set,
    )
    return [SkillEvaluationRunResponse.model_validate(run) for run in runs]


@router.post(
    "/{evaluation_set_id}/runs",
    response_model=SkillEvaluationRunResponse,
    status_code=201,
)
async def create_skill_evaluation_run(
    skill_id: uuid.UUID,
    evaluation_set_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> SkillEvaluationRunResponse:
    skill = await _load_skill_or_404(db, skill_id=skill_id, user=user)
    evaluation_set = await _load_evaluation_set_or_404(
        db,
        skill=skill,
        user=user,
        evaluation_set_id=evaluation_set_id,
    )
    run = await skill_evaluation_service.create_run(
        db,
        user_id=user.id,
        skill=skill,
        evaluation_set=evaluation_set,
    )
    await _record_evaluation_audit(
        db,
        user=user,
        request=request,
        action="skill_evaluation.run_create",
        skill_id=skill.id,
        evaluation_set_id=evaluation_set.id,
        run_id=run.id,
    )
    await db.commit()
    await db.refresh(run)
    return SkillEvaluationRunResponse.model_validate(run)


@router.post(
    "/{evaluation_set_id}/runs/{run_id}/cancel",
    response_model=SkillEvaluationRunResponse,
)
async def cancel_skill_evaluation_run(
    skill_id: uuid.UUID,
    evaluation_set_id: uuid.UUID,
    run_id: uuid.UUID,
    data: SkillEvaluationRunCancelRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> SkillEvaluationRunResponse:
    skill = await _load_skill_or_404(db, skill_id=skill_id, user=user)
    evaluation_set = await _load_evaluation_set_or_404(
        db,
        skill=skill,
        user=user,
        evaluation_set_id=evaluation_set_id,
    )
    run = await skill_evaluation_service.get_run(
        db,
        skill=skill,
        user_id=user.id,
        evaluation_set=evaluation_set,
        run_id=run_id,
    )
    if run is None:
        raise skill_evaluation_run_not_found()
    try:
        cancelled = await skill_evaluation_service.cancel_run(db, run, reason=data.reason)
    except skill_evaluation_service.SkillEvaluationRunNotCancellable as exc:
        raise skill_evaluation_run_not_cancellable() from exc
    await _record_evaluation_audit(
        db,
        user=user,
        request=request,
        action="skill_evaluation.run_cancel",
        skill_id=skill.id,
        evaluation_set_id=evaluation_set.id,
        run_id=cancelled.id,
    )
    await db.commit()
    await db.refresh(cancelled)
    return SkillEvaluationRunResponse.model_validate(cancelled)


async def _load_skill_or_404(
    db: AsyncSession,
    *,
    skill_id: uuid.UUID,
    user: CurrentUser,
) -> Skill:
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if skill is None:
        raise skill_not_found()
    return skill


async def _load_evaluation_set_or_404(
    db: AsyncSession,
    *,
    skill: Skill,
    user: CurrentUser,
    evaluation_set_id: uuid.UUID,
) -> SkillEvaluationSet:
    row = await skill_evaluation_service.get_evaluation_set(
        db,
        skill=skill,
        user_id=user.id,
        evaluation_set_id=evaluation_set_id,
    )
    if row is None:
        raise skill_evaluation_set_not_found()
    return row


async def _record_evaluation_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    action: str,
    skill_id: uuid.UUID,
    evaluation_set_id: uuid.UUID,
    run_id: uuid.UUID,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action=action,
        target_type="skill_evaluation_run",
        target_id=run_id,
        target_owner_user_id=user.id,
        outcome="success",
        request=request,
        metadata={
            "skill_id": str(skill_id),
            "evaluation_set_id": str(evaluation_set_id),
        },
    )
