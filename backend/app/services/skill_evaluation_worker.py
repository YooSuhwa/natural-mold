from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.database import async_session
from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.schemas.skill_builder import JsonValue
from app.services import audit_service
from app.services.skill_evaluation_worker_types import (
    DeterministicSkillEvaluationEvaluator,
    SkillEvaluationContext,
    SkillEvaluationEvaluator,
    SkillEvaluationExecutionError,
    SkillEvaluationResult,
)

logger = logging.getLogger(__name__)
type SkillEvaluationSessionFactory = async_sessionmaker[AsyncSession]


class SkillEvaluationQueueFull(RuntimeError):
    pass


@dataclass(slots=True)
class SkillEvaluationWorker:
    evaluator: SkillEvaluationEvaluator = field(
        default_factory=DeterministicSkillEvaluationEvaluator
    )
    queue_max_size: int = field(default_factory=lambda: settings.skill_evaluation_queue_max_size)
    _queue: deque[uuid.UUID] = field(default_factory=deque, init=False)
    _queued_ids: set[uuid.UUID] = field(default_factory=set, init=False)
    _reserved_slots: int = field(default=0, init=False)
    _session_factory: SkillEvaluationSessionFactory = field(default=async_session, init=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False)
    _stopping: asyncio.Event | None = field(default=None, init=False)
    _wake: asyncio.Event | None = field(default=None, init=False)

    def reserve_slot(self) -> None:
        if len(self._queue) + self._reserved_slots >= self.queue_max_size:
            raise SkillEvaluationQueueFull("skill evaluation queue is full")
        self._reserved_slots += 1

    def release_slot(self) -> None:
        self._reserved_slots = max(self._reserved_slots - 1, 0)

    def enqueue(self, run_id: uuid.UUID, *, reserved: bool = False) -> None:
        if run_id in self._queued_ids:
            if reserved:
                self.release_slot()
            return
        if not reserved and len(self._queue) + self._reserved_slots >= self.queue_max_size:
            raise SkillEvaluationQueueFull("skill evaluation queue is full")
        if reserved:
            self.release_slot()
        self._queue.append(run_id)
        self._queued_ids.add(run_id)
        if self._wake is not None:
            self._wake.set()

    def pop_next(self) -> uuid.UUID | None:
        if not self._queue:
            return None
        run_id = self._queue.popleft()
        self._queued_ids.discard(run_id)
        return run_id

    async def start(
        self,
        session_factory: SkillEvaluationSessionFactory | None = None,
    ) -> None:
        if session_factory is not None:
            self._session_factory = session_factory
        if self._task is not None and not self._task.done():
            return
        self._stopping = asyncio.Event()
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="skill-evaluation-worker")

    async def stop(self, *, timeout_seconds: float = 10.0) -> None:
        if self._stopping is not None:
            self._stopping.set()
        if self._wake is not None:
            self._wake.set()
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=timeout_seconds)
        except TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._task = None
        self._stopping = None
        self._wake = None

    async def reconcile_stale_runs(
        self,
        session_factory: SkillEvaluationSessionFactory | None = None,
    ) -> int:
        factory = session_factory or self._session_factory
        async with factory() as db:
            count = await self.mark_interrupted_runs(db)
            await db.commit()
            return count

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

    async def _loop(self) -> None:
        while self._stopping is None or not self._stopping.is_set():
            run_id = self.pop_next()
            if run_id is None:
                wake = self._wake
                if wake is None:
                    return
                await wake.wait()
                wake.clear()
                continue
            await self._execute_run(run_id)

    async def _execute_run(self, run_id: uuid.UUID) -> None:
        async with self._session_factory() as db:
            try:
                await self.run_once(db, run_id)
            except SkillEvaluationExecutionError as exc:
                await db.rollback()
                logger.warning("skill evaluation run skipped run_id=%s error=%s", run_id, exc)
                return
            await db.commit()


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
