from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.error_codes import (
    skill_evaluation_set_not_found,
    skill_not_found,
    system_llm_not_configured,
)
from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationSet
from app.schemas.skill_builder import JsonValue
from app.services import audit_service, skill_evaluation_service
from app.services.system_credential_resolver import (
    SystemModelNotConfiguredError,
    resolve_system_model,
)
from app.skills import service as skill_service


async def load_skill_or_404(
    db: AsyncSession,
    *,
    skill_id: uuid.UUID,
    user: CurrentUser,
) -> Skill:
    skill = await skill_service.get_skill(db, skill_id, user.id)
    if skill is None:
        raise skill_not_found()
    return skill


async def load_evaluation_set_or_404(
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


async def record_evaluation_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    action: str,
    skill_id: uuid.UUID,
    evaluation_set_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    outcome: str = "success",
    metadata: dict[str, JsonValue] | None = None,
) -> None:
    await audit_service.record_self_event(
        db,
        user,
        action=action,
        target_type="skill_evaluation_run" if run_id else "skill",
        target_id=run_id or skill_id,
        outcome=outcome,
        request=request,
        metadata={
            "skill_id": str(skill_id),
            "evaluation_set_id": str(evaluation_set_id) if evaluation_set_id else None,
            **(metadata or {}),
        },
    )


async def require_evaluation_system_llm(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    skill_id: uuid.UUID,
    evaluation_set_id: uuid.UUID,
) -> None:
    try:
        await resolve_system_model(db, "text_primary")
    except SystemModelNotConfiguredError as exc:
        await record_evaluation_audit(
            db,
            user=user,
            request=request,
            action="skill_evaluation.system_model_missing",
            skill_id=skill_id,
            evaluation_set_id=evaluation_set_id,
            outcome="denied",
            metadata={"reason_code": "SYSTEM_LLM_NOT_CONFIGURED"},
        )
        await db.commit()
        raise system_llm_not_configured() from exc
