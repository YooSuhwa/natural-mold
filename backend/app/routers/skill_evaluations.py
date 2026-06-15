from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import (
    marketplace_credential_required,
    skill_evaluation_queue_full,
    skill_evaluation_run_not_cancellable,
    skill_evaluation_run_not_found,
)
from app.marketplace import credential_requirements
from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.routers.skill_evaluations_support import (
    load_evaluation_set_or_404,
    load_skill_or_404,
    record_evaluation_audit,
    require_evaluation_system_llm,
)
from app.schemas.skill_evaluation import (
    SkillEvaluationRunCancelRequest,
    SkillEvaluationRunEstimate,
    SkillEvaluationRunResponse,
    SkillEvaluationSetCreate,
    SkillEvaluationSetResponse,
)
from app.services import skill_evaluation_service
from app.services.skill_evaluation_worker import (
    SkillEvaluationQueueFull,
    skill_evaluation_worker,
)

router = APIRouter(prefix="/api/skills/{skill_id}/evaluations", tags=["skill-evaluations"])


@router.get("", response_model=list[SkillEvaluationSetResponse])
async def list_skill_evaluations(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[SkillEvaluationSetResponse]:
    skill = await load_skill_or_404(db, skill_id=skill_id, user=user)
    rows = await skill_evaluation_service.list_evaluation_sets(db, skill=skill, user_id=user.id)
    latest_runs = await skill_evaluation_service.latest_runs_by_evaluation_set(
        db,
        skill=skill,
        user_id=user.id,
        evaluation_set_ids=[row.id for row in rows],
    )
    return [_evaluation_set_response(row, latest_runs.get(row.id)) for row in rows]


@router.post("", response_model=SkillEvaluationSetResponse, status_code=201)
async def create_skill_evaluation(
    skill_id: uuid.UUID,
    data: SkillEvaluationSetCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> SkillEvaluationSetResponse:
    skill = await load_skill_or_404(db, skill_id=skill_id, user=user)
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
    skill = await load_skill_or_404(db, skill_id=skill_id, user=user)
    row = await load_evaluation_set_or_404(
        db,
        skill=skill,
        user=user,
        evaluation_set_id=evaluation_set_id,
    )
    latest_runs = await skill_evaluation_service.latest_runs_by_evaluation_set(
        db,
        skill=skill,
        user_id=user.id,
        evaluation_set_ids=[row.id],
    )
    return _evaluation_set_response(row, latest_runs.get(row.id))


@router.post("/{evaluation_set_id}/estimate", response_model=SkillEvaluationRunEstimate)
async def estimate_skill_evaluation_run(
    skill_id: uuid.UUID,
    evaluation_set_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> SkillEvaluationRunEstimate:
    skill = await load_skill_or_404(db, skill_id=skill_id, user=user)
    evaluation_set = await load_evaluation_set_or_404(
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
    skill = await load_skill_or_404(db, skill_id=skill_id, user=user)
    evaluation_set = await load_evaluation_set_or_404(
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
    skill = await load_skill_or_404(db, skill_id=skill_id, user=user)
    evaluation_set = await load_evaluation_set_or_404(
        db,
        skill=skill,
        user=user,
        evaluation_set_id=evaluation_set_id,
    )
    missing = await credential_requirements.missing_required_keys(db, skill=skill, user=user)
    if missing:
        await record_evaluation_audit(
            db,
            user=user,
            request=request,
            action="skill_evaluation.credential_missing",
            skill_id=skill.id,
            evaluation_set_id=evaluation_set.id,
            outcome="denied",
            metadata={"missing_requirement_keys": missing},
        )
        await db.commit()
        raise marketplace_credential_required(
            f"missing required skill credential bindings: {', '.join(missing)}"
        )
    await require_evaluation_system_llm(
        db,
        user=user,
        request=request,
        skill_id=skill.id,
        evaluation_set_id=evaluation_set.id,
    )
    try:
        skill_evaluation_worker.reserve_slot()
    except SkillEvaluationQueueFull as exc:
        raise skill_evaluation_queue_full() from exc
    reserved_slot = True
    run = await skill_evaluation_service.create_run(
        db,
        user_id=user.id,
        skill=skill,
        evaluation_set=evaluation_set,
    )
    try:
        await record_evaluation_audit(
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
        skill_evaluation_worker.enqueue(run.id, reserved=True)
        reserved_slot = False
        return SkillEvaluationRunResponse.model_validate(run)
    finally:
        if reserved_slot:
            skill_evaluation_worker.release_slot()


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
    skill = await load_skill_or_404(db, skill_id=skill_id, user=user)
    evaluation_set = await load_evaluation_set_or_404(
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
    await record_evaluation_audit(
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


def _evaluation_set_response(
    row: SkillEvaluationSet,
    latest_run: SkillEvaluationRun | None,
) -> SkillEvaluationSetResponse:
    response = SkillEvaluationSetResponse.model_validate(row)
    if latest_run is None:
        return response
    return response.model_copy(
        update={"latest_run": SkillEvaluationRunResponse.model_validate(latest_run)}
    )
