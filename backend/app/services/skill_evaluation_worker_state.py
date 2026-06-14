from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.schemas.skill_builder import JsonValue
from app.services import audit_service
from app.services.skill_evaluation_worker_types import SkillEvaluationContext, SkillEvaluationResult


async def load_run(db: AsyncSession, run_id: uuid.UUID) -> SkillEvaluationRun | None:
    result = await db.execute(select(SkillEvaluationRun).where(SkillEvaluationRun.id == run_id))
    return result.scalar_one_or_none()


async def build_context(
    db: AsyncSession,
    run: SkillEvaluationRun,
) -> SkillEvaluationContext:
    result = await db.execute(
        select(SkillEvaluationSet).where(SkillEvaluationSet.id == run.evaluation_set_id)
    )
    evaluation_set = result.scalar_one()
    return SkillEvaluationContext(
        run_id=run.id,
        skill_id=run.skill_id,
        evaluation_set_id=run.evaluation_set_id,
        skill_version=run.skill_version,
        skill_content_hash=run.skill_content_hash,
        evals=evaluation_set.evals or [],
    )


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
) -> None:
    run.status = "completed"
    run.summary = result.summary
    run.benchmark = result.benchmark
    run.case_results = result.case_results
    run.error_message = None
    run.completed_at = _now()
    await db.flush()


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
