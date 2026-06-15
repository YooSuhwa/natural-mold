from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.marketplace import credential_requirements
from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.schemas.skill import SkillHealthSummary, SkillLatestEvaluationSummary
from app.services.skill_health_service import calculate_skill_health


@dataclass(frozen=True)
class SkillQualitySummary:
    latest_evaluation_summary: SkillLatestEvaluationSummary
    health: SkillHealthSummary


async def build_skill_quality_map(
    db: AsyncSession,
    *,
    user: CurrentUser,
    skills: list[Skill],
) -> dict[uuid.UUID, SkillQualitySummary]:
    latest_runs = await _latest_runs_by_skill(db, user_id=user.id, skill_ids=[s.id for s in skills])
    latest_sets = await _latest_sets_by_skill(db, user_id=user.id, skill_ids=[s.id for s in skills])
    summaries: dict[uuid.UUID, SkillQualitySummary] = {}
    for skill in skills:
        latest_run = latest_runs.get(skill.id)
        latest_set = latest_sets.get(skill.id)
        missing = await credential_requirements.missing_required_keys(db, skill=skill, user=user)
        summaries[skill.id] = SkillQualitySummary(
            latest_evaluation_summary=_summarize_latest_evaluation(skill, latest_run, latest_set),
            health=SkillHealthSummary.model_validate(
                calculate_skill_health(
                    skill,
                    latest_run=latest_run,
                    missing_required_keys=missing,
                )
            ),
        )
    return summaries


async def _latest_runs_by_skill(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    skill_ids: list[uuid.UUID],
) -> dict[uuid.UUID, SkillEvaluationRun]:
    if not skill_ids:
        return {}
    result = await db.execute(
        select(SkillEvaluationRun)
        .where(SkillEvaluationRun.user_id == user_id, SkillEvaluationRun.skill_id.in_(skill_ids))
        .order_by(SkillEvaluationRun.skill_id, desc(SkillEvaluationRun.created_at))
    )
    latest: dict[uuid.UUID, SkillEvaluationRun] = {}
    for run in result.scalars():
        latest.setdefault(run.skill_id, run)
    return latest


async def _latest_sets_by_skill(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    skill_ids: list[uuid.UUID],
) -> dict[uuid.UUID, SkillEvaluationSet]:
    if not skill_ids:
        return {}
    result = await db.execute(
        select(SkillEvaluationSet)
        .where(SkillEvaluationSet.user_id == user_id, SkillEvaluationSet.skill_id.in_(skill_ids))
        .order_by(SkillEvaluationSet.skill_id, desc(SkillEvaluationSet.updated_at))
    )
    latest: dict[uuid.UUID, SkillEvaluationSet] = {}
    for evaluation_set in result.scalars():
        latest.setdefault(evaluation_set.skill_id, evaluation_set)
    return latest


def _summarize_latest_evaluation(
    skill: Skill,
    run: SkillEvaluationRun | None,
    latest_set: SkillEvaluationSet | None,
) -> SkillLatestEvaluationSummary:
    if run is None:
        return SkillLatestEvaluationSummary(
            status="missing",
            evaluation_set_id=latest_set.id if latest_set is not None else None,
        )

    pass_rate = _pass_rate(run)
    status = _summary_status(skill, run, pass_rate)
    return SkillLatestEvaluationSummary(
        status=status,
        latest_run_id=run.id,
        evaluation_set_id=run.evaluation_set_id,
        pass_rate=pass_rate,
        skill_content_hash=run.skill_content_hash,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


def _summary_status(
    skill: Skill,
    run: SkillEvaluationRun,
    pass_rate: float | None,
) -> str:
    if run.skill_content_hash != skill.content_hash:
        return "stale"
    if run.status == "completed":
        return "passed" if pass_rate is not None and pass_rate >= 0.8 else "partial"
    return run.status


def _pass_rate(run: SkillEvaluationRun) -> float | None:
    summary = run.summary or {}
    value = summary.get("pass_rate")
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None
