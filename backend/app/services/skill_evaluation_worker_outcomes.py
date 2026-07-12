from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.skill_evaluation import SkillEvaluationRun
from app.services.skill_evaluation_worker_state import (
    mark_cancelled,
    mark_failed,
    record_run_audit,
)
from app.services.skill_evaluation_worker_types import (
    SkillEvaluationExecutionError,
    SkillEvaluationResult,
)


async def cancel_if_requested(db: AsyncSession, run: SkillEvaluationRun) -> bool:
    await db.refresh(run)
    if run.status != "cancelled" and run.cancellation_requested_at is None:
        return False
    await mark_cancelled(db, run)
    await db.flush()
    return True


async def mark_timeout_failed(
    db: AsyncSession,
    run: SkillEvaluationRun,
    timeout_seconds: float | None = None,
) -> None:
    # Report the timeout that actually fired (scaled to the run's workload),
    # not the fixed floor.
    effective = (
        timeout_seconds
        if timeout_seconds is not None
        else settings.skill_evaluation_run_timeout_seconds
    )
    timeout_message = f"timeout: evaluation run exceeded {effective}s"
    await mark_failed(db, run, timeout_message)
    await record_run_audit(
        db,
        run,
        "skill_evaluation.run_fail",
        outcome="failure",
        metadata={"reason_code": "SKILL_EVALUATION_TIMEOUT"},
    )
    await db.flush()


async def mark_execution_error_failed(
    db: AsyncSession,
    run: SkillEvaluationRun,
    exc: SkillEvaluationExecutionError,
) -> None:
    await mark_failed(db, run, str(exc))
    await record_run_audit(
        db,
        run,
        "skill_evaluation.run_fail",
        outcome="failure",
        metadata={"reason_code": "SKILL_EVALUATION_EXECUTION_ERROR"},
    )
    await db.flush()


async def record_completed_audit(
    db: AsyncSession,
    run: SkillEvaluationRun,
    result: SkillEvaluationResult,
) -> None:
    summary = result.summary
    await record_run_audit(
        db,
        run,
        "skill_evaluation.run_complete",
        metadata={
            "case_count": summary.get("case_count"),
            "passed_count": summary.get("passed_count"),
            "failed_count": summary.get("failed_count"),
            "pass_rate": summary.get("pass_rate"),
        },
    )
