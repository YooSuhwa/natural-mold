from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.marketplace import credential_requirements
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent
from app.models.marketplace import SkillCredentialBinding
from app.models.skill import AgentSkillLink, Skill
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
    missing_keys = await _missing_required_keys_by_skill(db, user=user, skills=skills)
    summaries: dict[uuid.UUID, SkillQualitySummary] = {}
    for skill in skills:
        latest_run = latest_runs.get(skill.id)
        latest_set = latest_sets.get(skill.id)
        missing = missing_keys.get(skill.id, [])
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


async def agent_link_counts_by_skill(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    skill_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    """스킬별 연결 에이전트 수 — 단일 GROUP BY 역집계.

    ``Skill.used_by_count`` 컬럼은 쓰기 동기화가 없어(생성 시 0 고정) 신뢰할
    수 없다 — 직렬화 시점에 이 집계로 덮어쓴다. 히든 에이전트
    (``runtime_profile != 'standard'``)는 다른 모든 표면과 동일하게 제외.
    """

    if not skill_ids:
        return {}
    result = await db.execute(
        select(AgentSkillLink.skill_id, func.count())
        .join(Agent, Agent.id == AgentSkillLink.agent_id)
        .where(
            Agent.user_id == user_id,
            Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
            AgentSkillLink.skill_id.in_(skill_ids),
        )
        .group_by(AgentSkillLink.skill_id)
    )
    return {skill_id: int(count) for skill_id, count in result.all()}


async def _missing_required_keys_by_skill(
    db: AsyncSession,
    *,
    user: CurrentUser,
    skills: list[Skill],
) -> dict[uuid.UUID, list[str]]:
    skill_ids = [skill.id for skill in skills]
    required_by_skill = {
        skill.id: [
            requirement.key
            for requirement in credential_requirements.parse_requirements(skill)
            if requirement.required and requirement.scope == "user"
        ]
        for skill in skills
    }
    lookup_ids = [skill_id for skill_id, keys in required_by_skill.items() if keys]
    if not lookup_ids:
        return {skill_id: [] for skill_id in skill_ids}
    result = await db.execute(
        select(SkillCredentialBinding).where(
            SkillCredentialBinding.skill_id.in_(lookup_ids),
            SkillCredentialBinding.user_id == user.id,
            SkillCredentialBinding.scope == "skill",
        )
    )
    bound_by_skill: dict[uuid.UUID, set[str]] = {}
    for binding in result.scalars():
        bound_by_skill.setdefault(binding.skill_id, set()).add(binding.requirement_key)
    return {
        skill_id: [key for key in keys if key not in bound_by_skill.get(skill_id, set())]
        for skill_id, keys in required_by_skill.items()
    }


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
