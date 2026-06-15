from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.eval_limits import MAX_SKILL_EVAL_CASES, MIN_SKILL_EVAL_CASES
from app.config import settings
from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.schemas.skill_evaluation import SkillEvaluationRunEstimate
from app.services.skill_evaluation_case_limits import (
    SkillEvaluationCaseSizeError,
    validate_evaluation_case_sizes,
)

CANCELLABLE_STATUSES = frozenset({"queued", "running", "grading"})


class SkillEvaluationRunNotCancellable(RuntimeError):
    pass


class SkillEvaluationSetTooLarge(ValueError):
    pass


class SkillEvaluationSetEmpty(ValueError):
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
    if len(evals) < MIN_SKILL_EVAL_CASES:
        raise SkillEvaluationSetEmpty("evaluation sets require at least one case")
    if len(evals) > MAX_SKILL_EVAL_CASES:
        raise SkillEvaluationSetTooLarge(
            f"evaluation sets can contain at most {MAX_SKILL_EVAL_CASES} cases"
        )
    try:
        validate_evaluation_case_sizes(evals)
    except SkillEvaluationCaseSizeError as exc:
        raise SkillEvaluationSetTooLarge(str(exc)) from exc
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


async def list_evaluation_sets(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
) -> list[SkillEvaluationSet]:
    result = await db.execute(
        select(SkillEvaluationSet)
        .where(SkillEvaluationSet.skill_id == skill.id, SkillEvaluationSet.user_id == user_id)
        .order_by(desc(SkillEvaluationSet.updated_at), desc(SkillEvaluationSet.created_at))
    )
    return list(result.scalars().all())


async def get_evaluation_set(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    evaluation_set_id: uuid.UUID,
) -> SkillEvaluationSet | None:
    result = await db.execute(
        select(SkillEvaluationSet).where(
            SkillEvaluationSet.id == evaluation_set_id,
            SkillEvaluationSet.skill_id == skill.id,
            SkillEvaluationSet.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def list_runs(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    evaluation_set: SkillEvaluationSet,
) -> list[SkillEvaluationRun]:
    result = await db.execute(
        select(SkillEvaluationRun)
        .where(
            SkillEvaluationRun.skill_id == skill.id,
            SkillEvaluationRun.user_id == user_id,
            SkillEvaluationRun.evaluation_set_id == evaluation_set.id,
        )
        .order_by(desc(SkillEvaluationRun.created_at))
    )
    return list(result.scalars().all())


async def latest_runs_by_evaluation_set(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    evaluation_set_ids: list[uuid.UUID],
) -> dict[uuid.UUID, SkillEvaluationRun]:
    if not evaluation_set_ids:
        return {}
    result = await db.execute(
        select(SkillEvaluationRun)
        .where(
            SkillEvaluationRun.skill_id == skill.id,
            SkillEvaluationRun.user_id == user_id,
            SkillEvaluationRun.evaluation_set_id.in_(evaluation_set_ids),
        )
        .order_by(SkillEvaluationRun.evaluation_set_id, desc(SkillEvaluationRun.created_at))
    )
    latest: dict[uuid.UUID, SkillEvaluationRun] = {}
    for run in result.scalars():
        latest.setdefault(run.evaluation_set_id, run)
    return latest


async def get_run(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    evaluation_set: SkillEvaluationSet,
    run_id: uuid.UUID,
) -> SkillEvaluationRun | None:
    result = await db.execute(
        select(SkillEvaluationRun).where(
            SkillEvaluationRun.id == run_id,
            SkillEvaluationRun.skill_id == skill.id,
            SkillEvaluationRun.user_id == user_id,
            SkillEvaluationRun.evaluation_set_id == evaluation_set.id,
        )
    )
    return result.scalar_one_or_none()


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
    result = await db.execute(
        update(SkillEvaluationRun)
        .where(
            SkillEvaluationRun.id == run.id,
            SkillEvaluationRun.status.in_(CANCELLABLE_STATUSES),
        )
        .values(
            status="cancelled",
            cancellation_requested_at=now,
            cancellation_reason=reason[:120],
            completed_at=now,
        )
    )
    await db.flush()
    if result.rowcount != 1:
        await db.refresh(run)
        raise SkillEvaluationRunNotCancellable(f"run status is not cancellable: {run.status}")
    await db.refresh(run)
    return run


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
