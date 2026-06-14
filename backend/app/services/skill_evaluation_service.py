from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.schemas.skill_evaluation import SkillEvaluationRunEstimate

CANCELLABLE_STATUSES = frozenset({"queued", "running", "grading"})


class SkillEvaluationRunNotCancellable(RuntimeError):
    pass


async def create_evaluation_set(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    skill: Skill,
    name: str,
    evals: list[Any],
    description: str | None = None,
    source_kind: str = "builder",
    template_key: str | None = None,
    template_version: str | None = None,
    generation_strategy: dict[str, Any] | None = None,
) -> SkillEvaluationSet:
    evaluation_set = SkillEvaluationSet(
        user_id=user_id,
        skill_id=skill.id,
        name=name,
        description=description,
        source_kind=source_kind,
        template_key=template_key,
        template_version=template_version,
        generation_strategy=generation_strategy,
        evals=evals,
    )
    db.add(evaluation_set)
    await db.flush()
    return evaluation_set


def estimate_run(
    evaluation_set: SkillEvaluationSet,
    *,
    uses_baseline_comparison: bool = True,
) -> SkillEvaluationRunEstimate:
    case_count = len(evaluation_set.evals or [])
    model_calls_per_case = 3 if uses_baseline_comparison else 2
    estimated_seconds = min(
        settings.skill_evaluation_run_timeout_seconds,
        case_count * settings.skill_evaluation_case_timeout_seconds,
    )
    return SkillEvaluationRunEstimate(
        case_count=case_count,
        model_call_count=case_count * model_calls_per_case,
        estimated_seconds=estimated_seconds,
        timeout_seconds=settings.skill_evaluation_run_timeout_seconds,
        estimated_cost_usd=0,
        uses_baseline_comparison=uses_baseline_comparison,
    )


async def create_run(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    skill: Skill,
    evaluation_set: SkillEvaluationSet,
    run_config: dict[str, Any] | None = None,
) -> SkillEvaluationRun:
    estimate = estimate_run(evaluation_set)
    run = SkillEvaluationRun(
        user_id=user_id,
        skill_id=skill.id,
        evaluation_set_id=evaluation_set.id,
        status="queued",
        skill_version=skill.version,
        skill_content_hash=skill.content_hash,
        run_config=run_config,
        estimate=estimate.model_dump(mode="json"),
    )
    db.add(run)
    await db.flush()
    return run


async def cancel_run(
    db: AsyncSession,
    run: SkillEvaluationRun,
    *,
    reason: str,
) -> SkillEvaluationRun:
    if run.status not in CANCELLABLE_STATUSES:
        raise SkillEvaluationRunNotCancellable(f"run status is not cancellable: {run.status}")
    now = _now()
    run.status = "cancelled"
    run.cancellation_requested_at = now
    run.cancellation_reason = reason[:120]
    run.completed_at = now
    await db.flush()
    return run


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
