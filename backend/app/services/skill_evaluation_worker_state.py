from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, undefer

from app.agent_runtime.skill_builder.eval_cancellation import (
    EvalCancellationCheckpoint,
    EvalRunCancelled,
)
from app.authz.scoping import PrivateParent, private_parent_scoped
from app.authz.skill_binding_scope import private_skill_child_predicate
from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.schemas.skill_builder import JsonValue
from app.services import audit_service
from app.services.skill_evaluation_access import SkillEvaluationAccessPlanner
from app.services.skill_evaluation_worker_types import (
    SkillEvaluationContext,
    SkillEvaluationExecutionError,
    SkillEvaluationResult,
)

RUN_LIFECYCLE_OPTIONS: Final = load_only(
    SkillEvaluationRun.id,
    SkillEvaluationRun.user_id,
    SkillEvaluationRun.skill_id,
    SkillEvaluationRun.evaluation_set_id,
    SkillEvaluationRun.status,
    SkillEvaluationRun.skill_version,
    SkillEvaluationRun.skill_content_hash,
    SkillEvaluationRun.started_at,
    SkillEvaluationRun.completed_at,
    SkillEvaluationRun.cancellation_requested_at,
    raiseload=True,
)


async def load_run(db: AsyncSession, run_id: uuid.UUID) -> SkillEvaluationRun | None:
    result = await db.execute(
        select(SkillEvaluationRun)
        .options(RUN_LIFECYCLE_OPTIONS)
        .where(SkillEvaluationRun.id == run_id)
        .execution_options(populate_existing=True, autoflush=False)
    )
    return result.scalar_one_or_none()


@dataclass(frozen=True, slots=True)
class DbSkillEvaluationCancellationProbe:
    db: AsyncSession
    run_id: uuid.UUID

    async def raise_if_cancelled(self, checkpoint: EvalCancellationCheckpoint) -> None:
        run = (
            await self.db.execute(
                select(SkillEvaluationRun.status, SkillEvaluationRun.cancellation_requested_at)
                .where(SkillEvaluationRun.id == self.run_id)
                .execution_options(autoflush=False)
            )
        ).one_or_none()
        if run is None:
            raise SkillEvaluationExecutionError(f"run not found during evaluation: {self.run_id}")
        if run.status == "cancelled" or run.cancellation_requested_at is not None:
            raise EvalRunCancelled(checkpoint)


async def build_context(
    db: AsyncSession,
    run: SkillEvaluationRun,
) -> SkillEvaluationContext:
    planner = SkillEvaluationAccessPlanner()  # F-13: re-decided when the run executes
    target = await planner.authorize(db, run)
    result = await db.execute(
        private_parent_scoped(
            select(SkillEvaluationSet),
            PrivateParent(Skill, Skill.id == SkillEvaluationSet.skill_id),
            target.organization,
        )
        .where(
            SkillEvaluationSet.id == run.evaluation_set_id,
            SkillEvaluationSet.skill_id == run.skill_id,
            private_skill_child_predicate(SkillEvaluationSet, target.organization).correlate(
                SkillEvaluationSet
            ),
        )
        .execution_options(populate_existing=True, autoflush=False)
    )
    evaluation_set = result.scalar_one_or_none()
    if evaluation_set is None or (evaluation_set.skill_id, evaluation_set.user_id) != (
        run.skill_id,
        run.user_id,
    ):
        raise SkillEvaluationExecutionError("evaluation set is unavailable for this run")
    authorized_run = await db.scalar(
        private_parent_scoped(
            select(SkillEvaluationRun).options(undefer("*")),
            PrivateParent(Skill, Skill.id == SkillEvaluationRun.skill_id),
            target.organization,
        )
        .where(
            SkillEvaluationRun.id == run.id,
            SkillEvaluationRun.skill_id == run.skill_id,
            SkillEvaluationRun.evaluation_set_id == run.evaluation_set_id,
            private_skill_child_predicate(SkillEvaluationRun, target.organization).correlate(
                SkillEvaluationRun
            ),
        )
        .execution_options(populate_existing=True)
    )
    if authorized_run is None:
        raise SkillEvaluationExecutionError("evaluation set is unavailable for this run")
    runtime_context = await planner.runtime_context(db, run, target)
    return SkillEvaluationContext(
        run_id=run.id,
        skill_id=run.skill_id,
        evaluation_set_id=run.evaluation_set_id,
        skill_version=run.skill_version,
        skill_content_hash=run.skill_content_hash,
        evals=evaluation_set.evals or [],
        runtime_context=runtime_context,
        cancellation=DbSkillEvaluationCancellationProbe(db=db, run_id=run.id),
        baseline_comparison=_baseline_comparison(authorized_run.run_config),
    )


def _baseline_comparison(run_config: dict | None) -> bool:
    if isinstance(run_config, dict):
        value = run_config.get("baseline_comparison")
        if isinstance(value, bool):
            return value
    return True


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
    result_row = cast(
        CursorResult[Any],
        await db.execute(
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
        ),
    )
    await db.flush()
    await db.refresh(run, attribute_names=list(SkillEvaluationRun.__table__.columns.keys()))
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
