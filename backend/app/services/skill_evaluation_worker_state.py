from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.skill_builder.eval_cancellation import (
    EvalCancellationCheckpoint,
    EvalRunCancelled,
)
from app.config import settings
from app.exceptions import AppError
from app.marketplace.skill_runtime import (
    SkillToolContext,
    build_skill_runtime_context,
    resolve_runtime_credentials,
)
from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.schemas.skill_builder import JsonValue
from app.services import audit_service
from app.services.skill_evaluation_worker_types import (
    SkillEvaluationContext,
    SkillEvaluationExecutionError,
    SkillEvaluationResult,
)
from app.skills import service as skill_service


async def load_run(db: AsyncSession, run_id: uuid.UUID) -> SkillEvaluationRun | None:
    result = await db.execute(select(SkillEvaluationRun).where(SkillEvaluationRun.id == run_id))
    return result.scalar_one_or_none()


@dataclass(frozen=True, slots=True)
class DbSkillEvaluationCancellationProbe:
    db: AsyncSession
    run_id: uuid.UUID

    async def raise_if_cancelled(self, checkpoint: EvalCancellationCheckpoint) -> None:
        run = await self.db.get(SkillEvaluationRun, self.run_id, populate_existing=True)
        if run is None:
            raise SkillEvaluationExecutionError(f"run not found during evaluation: {self.run_id}")
        if run.status == "cancelled" or run.cancellation_requested_at is not None:
            raise EvalRunCancelled(checkpoint)


async def build_context(
    db: AsyncSession,
    run: SkillEvaluationRun,
) -> SkillEvaluationContext:
    result = await db.execute(
        select(SkillEvaluationSet).where(SkillEvaluationSet.id == run.evaluation_set_id)
    )
    evaluation_set = result.scalar_one()
    runtime_context = await _build_runtime_context(db, run)
    return SkillEvaluationContext(
        run_id=run.id,
        skill_id=run.skill_id,
        evaluation_set_id=run.evaluation_set_id,
        skill_version=run.skill_version,
        skill_content_hash=run.skill_content_hash,
        evals=evaluation_set.evals or [],
        runtime_context=runtime_context,
        cancellation=DbSkillEvaluationCancellationProbe(db=db, run_id=run.id),
    )


async def _build_runtime_context(
    db: AsyncSession,
    run: SkillEvaluationRun,
) -> SkillToolContext:
    skill = await db.get(Skill, run.skill_id)
    if skill is None:
        raise SkillEvaluationExecutionError(f"skill not found for evaluation: {run.skill_id}")

    descriptor = skill_service.to_runtime_dict(skill)
    if skill.execution_profile:
        descriptor["execution_profile"] = skill.execution_profile

    cfg = AgentConfig(
        provider="evaluation",
        model_name="evaluation",
        api_key=None,
        base_url=None,
        system_prompt="",
        tools_config=[],
        thread_id=str(run.id),
        agent_skills=[descriptor],
        user_id=str(run.user_id),
        credential_subject_user_id=str(run.user_id),
    )
    data_dir = Path(settings.data_root)
    context = build_skill_runtime_context(
        cfg,
        data_dir=data_dir,
        output_root=data_dir / "skill-evaluation-runs",
    )
    context.run_id = str(run.id)
    context.audit_kind = "skill_evaluation"
    try:
        await resolve_runtime_credentials(context, db=db, cfg=cfg)
    except AppError as exc:
        raise SkillEvaluationExecutionError(exc.message) from exc
    return context


async def mark_running(db: AsyncSession, run: SkillEvaluationRun) -> None:
    run.status = "running"
    run.started_at = _now()
    await db.flush()


async def mark_grading(db: AsyncSession, run: SkillEvaluationRun) -> None:
    run.status = "grading"
    await db.flush()


async def mark_completed(
    db: AsyncSession,
    run: SkillEvaluationRun,
    result: SkillEvaluationResult,
) -> bool:
    completed_at = _now()
    result_row = await db.execute(
        update(SkillEvaluationRun)
        .where(
            SkillEvaluationRun.id == run.id,
            SkillEvaluationRun.status != "cancelled",
            SkillEvaluationRun.cancellation_requested_at.is_(None),
        )
        .values(
            status="completed",
            summary=result.summary,
            benchmark=result.benchmark,
            case_results=result.case_results,
            runner_model=result.runner_model,
            runner_version=result.runner_version,
            grader_prompt_version=result.grader_prompt_version,
            eval_schema_version=result.eval_schema_version,
            usage=result.usage,
            error_message=None,
            completed_at=completed_at,
        )
    )
    await db.flush()
    await db.refresh(run)
    return result_row.rowcount == 1


async def mark_failed(db: AsyncSession, run: SkillEvaluationRun, message: str) -> None:
    run.status = "failed"
    run.error_message = message[:500]
    run.completed_at = _now()
    await db.flush()


async def mark_cancelled(db: AsyncSession, run: SkillEvaluationRun) -> None:
    run.status = "cancelled"
    run.completed_at = run.completed_at or _now()
    await db.flush()


async def record_run_audit(
    db: AsyncSession,
    run: SkillEvaluationRun,
    action: str,
    *,
    outcome: str = "success",
    metadata: dict[str, JsonValue] | None = None,
) -> None:
    await audit_service.record_event(
        db,
        actor_type="system",
        actor_label="skill-evaluation-worker",
        owner_user_id=run.user_id,
        action=action,
        target_type="skill_evaluation_run",
        target_id=run.id,
        target_owner_user_id=run.user_id,
        outcome=outcome,
        run_id=run.id,
        metadata={
            "skill_id": str(run.skill_id),
            "evaluation_set_id": str(run.evaluation_set_id),
            **(metadata or {}),
        },
    )


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
