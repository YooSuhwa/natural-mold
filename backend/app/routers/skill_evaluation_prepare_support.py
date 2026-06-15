from __future__ import annotations

import uuid
from typing import assert_never

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.schemas.skill_builder import JsonValue
from app.schemas.skill_evaluation import SkillEvaluationPrepareResponse
from app.services.skill_evaluation_set_preparation import (
    SkillEvaluationPreparationResult,
    SkillEvaluationPreparationStatus,
)

from .skill_evaluations_support import record_evaluation_audit


def preparation_response(
    result: SkillEvaluationPreparationResult,
) -> SkillEvaluationPrepareResponse:
    return SkillEvaluationPrepareResponse(
        status=result.status.value,
        evaluation_set_id=result.evaluation_set_id,
        source_kind=result.source_kind,
        case_count=result.case_count,
        payload_hash=result.payload_hash,
        reason=result.reason,
    )


async def record_preparation_audit(
    db: AsyncSession,
    *,
    user: CurrentUser,
    request: Request,
    skill_id: uuid.UUID,
    result: SkillEvaluationPreparationResult,
) -> None:
    await record_evaluation_audit(
        db,
        user=user,
        request=request,
        action=_audit_action(result),
        skill_id=skill_id,
        evaluation_set_id=result.evaluation_set_id,
        outcome=_audit_outcome(result),
        metadata=_audit_metadata(result),
    )


def _audit_action(result: SkillEvaluationPreparationResult) -> str:
    match result.status:
        case SkillEvaluationPreparationStatus.CREATED:
            if result.source_kind == "llm_generated":
                return "skill_evaluation_set.generated"
            return "skill_evaluation_set.imported"
        case SkillEvaluationPreparationStatus.SKIPPED_DUPLICATE:
            return "skill_evaluation_set.prepare_skipped"
        case SkillEvaluationPreparationStatus.SKIPPED_NO_EVALS:
            return "skill_evaluation_set.prepare_skipped"
        case SkillEvaluationPreparationStatus.SKIPPED_NO_SYSTEM_MODEL:
            return "skill_evaluation_set.prepare_skipped"
        case SkillEvaluationPreparationStatus.FAILED:
            return "skill_evaluation_set.prepare_failed"
        case unreachable:
            assert_never(unreachable)


def _audit_outcome(result: SkillEvaluationPreparationResult) -> str:
    match result.status:
        case SkillEvaluationPreparationStatus.FAILED:
            return "failure"
        case SkillEvaluationPreparationStatus.CREATED:
            return "success"
        case SkillEvaluationPreparationStatus.SKIPPED_DUPLICATE:
            return "success"
        case SkillEvaluationPreparationStatus.SKIPPED_NO_EVALS:
            return "success"
        case SkillEvaluationPreparationStatus.SKIPPED_NO_SYSTEM_MODEL:
            return "success"
        case unreachable:
            assert_never(unreachable)


def _audit_metadata(result: SkillEvaluationPreparationResult) -> dict[str, JsonValue]:
    return {
        "status": result.status.value,
        "source_kind": result.source_kind,
        "case_count": result.case_count,
        "payload_hash": result.payload_hash,
        "reason": result.reason,
    }
