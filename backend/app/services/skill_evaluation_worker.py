from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.schemas.skill_builder import JsonValue
from app.services import audit_service


class SkillEvaluationQueueFull(RuntimeError):
    pass


class SkillEvaluationExecutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SkillEvaluationContext:
    run_id: uuid.UUID
    skill_id: uuid.UUID
    evaluation_set_id: uuid.UUID
    skill_version: str | None
    skill_content_hash: str | None
    evals: Sequence[JsonValue]


@dataclass(frozen=True, slots=True)
class SkillEvaluationResult:
    summary: dict[str, JsonValue]
    benchmark: dict[str, JsonValue] | None = None
    case_results: list[JsonValue] | None = None


class SkillEvaluationEvaluator(Protocol):
    async def evaluate(self, context: SkillEvaluationContext) -> SkillEvaluationResult: ...


@dataclass(slots=True)
class DeterministicSkillEvaluationEvaluator:
    runner_version: str = "deterministic-1"

    async def evaluate(self, context: SkillEvaluationContext) -> SkillEvaluationResult:
        case_results: list[JsonValue] = [
            {
                "case_index": index,
                "status": "passed",
                "input": case,
                "score": 1,
                "notes": "Deterministic placeholder result.",
            }
            for index, case in enumerate(context.evals)
        ]
        case_count = len(case_results)
        pass_rate = 1 if case_count else 0
        return SkillEvaluationResult(
            summary={
                "runner_version": self.runner_version,
                "case_count": case_count,
                "passed_count": case_count,
                "failed_count": 0,
                "pass_rate": pass_rate,
            },
            benchmark={"baseline": "none", "score_delta": 0},
            case_results=case_results,
        )


@dataclass(slots=True)
class SkillEvaluationWorker:
    evaluator: SkillEvaluationEvaluator = field(
        default_factory=DeterministicSkillEvaluationEvaluator
    )
    queue_max_size: int = field(default_factory=lambda: settings.skill_evaluation_queue_max_size)
    _queue: deque[uuid.UUID] = field(default_factory=deque, init=False)
    _queued_ids: set[uuid.UUID] = field(default_factory=set, init=False)

    def enqueue(self, run_id: uuid.UUID) -> None:
        if run_id in self._queued_ids:
            return
        if len(self._queue) >= self.queue_max_size:
            raise SkillEvaluationQueueFull("skill evaluation queue is full")
        self._queue.append(run_id)
        self._queued_ids.add(run_id)

    def pop_next(self) -> uuid.UUID | None:
        if not self._queue:
            return None
        run_id = self._queue.popleft()
        self._queued_ids.discard(run_id)
        return run_id

    async def run_once(self, db: AsyncSession, run_id: uuid.UUID) -> SkillEvaluationRun | None:
        run = await _load_run(db, run_id)
        if run is None:
            return None
        if run.status == "cancelled":
            return run
        if run.status != "queued":
            raise SkillEvaluationExecutionError(f"run is not queued: {run.status}")

        await _mark_running(db, run)
        await _record_run_audit(db, run, "skill_evaluation.run_start")
        await _mark_grading(db, run)
        try:
            result = await self.evaluator.evaluate(await _build_context(db, run))
        except SkillEvaluationExecutionError as exc:
            await _mark_failed(db, run, str(exc))
            await _record_run_audit(
                db,
                run,
                "skill_evaluation.run_fail",
                outcome="failure",
                metadata={"error_message": str(exc)},
            )
            await db.flush()
            return run

        await db.refresh(run)
        if run.status == "cancelled" or run.cancellation_requested_at is not None:
            await _mark_cancelled(db, run)
            await db.flush()
            return run

        await _mark_completed(db, run, result)
        await _record_run_audit(
            db,
            run,
            "skill_evaluation.run_complete",
            metadata={"case_count": result.summary.get("case_count")},
        )
        await db.flush()
        return run

    async def mark_interrupted_runs(self, db: AsyncSession) -> int:
        result = await db.execute(
            select(SkillEvaluationRun).where(SkillEvaluationRun.status.in_(("running", "grading")))
        )
        rows = list(result.scalars().all())
        for run in rows:
            await _mark_failed(db, run, "interrupted: process restarted")
            await _record_run_audit(
                db,
                run,
                "skill_evaluation.run_fail",
                outcome="failure",
                metadata={"reason": "worker_restart"},
            )
        await db.flush()
        return len(rows)


async def _load_run(db: AsyncSession, run_id: uuid.UUID) -> SkillEvaluationRun | None:
    result = await db.execute(select(SkillEvaluationRun).where(SkillEvaluationRun.id == run_id))
    return result.scalar_one_or_none()


async def _build_context(
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


async def _mark_running(db: AsyncSession, run: SkillEvaluationRun) -> None:
    run.status = "running"
    run.started_at = _now()
    await db.flush()


async def _mark_grading(db: AsyncSession, run: SkillEvaluationRun) -> None:
    run.status = "grading"
    await db.flush()


async def _mark_completed(
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


async def _mark_failed(db: AsyncSession, run: SkillEvaluationRun, message: str) -> None:
    run.status = "failed"
    run.error_message = message[:500]
    run.completed_at = _now()
    await db.flush()


async def _mark_cancelled(db: AsyncSession, run: SkillEvaluationRun) -> None:
    run.status = "cancelled"
    run.completed_at = run.completed_at or _now()
    await db.flush()


async def _record_run_audit(
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


skill_evaluation_worker = SkillEvaluationWorker()
