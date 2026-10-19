from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.eval_cancellation import EvalRunCancelled
from app.config import settings
from app.database import async_session
from app.models.skill_evaluation import SkillEvaluationRun
from app.services.skill_evaluation_llm import LlmSkillEvaluationEvaluator
from app.services.skill_evaluation_queue import (
    SkillEvaluationQueue,
    SkillEvaluationQueueFull,
    SkillEvaluationQueueItem,
)
from app.services.skill_evaluation_service import effective_run_timeout_seconds
from app.services.skill_evaluation_usage_ledger import record_evaluation_usage_nonfatal
from app.services.skill_evaluation_worker_leader import (
    release_skill_evaluation_worker_leader,
    try_acquire_skill_evaluation_worker_leader,
)
from app.services.skill_evaluation_worker_outcomes import (
    cancel_if_requested,
    mark_execution_error_failed,
    mark_timeout_failed,
    record_completed_audit,
)
from app.services.skill_evaluation_worker_state import (
    build_context,
    load_run,
    mark_cancelled,
    mark_completed,
    mark_grading,
    mark_running,
    record_run_audit,
)
from app.services.skill_evaluation_worker_tenants import (
    company_session,
    mark_interrupted_runs,
    reconcile_interrupted_by_company,
)
from app.services.skill_evaluation_worker_types import (
    SkillEvaluationEvaluator,
    SkillEvaluationExecutionError,
)

logger = logging.getLogger(__name__)


class SkillEvaluationSessionFactory(Protocol):
    """Construct an async session, including project-specific subclasses."""

    def __call__(self) -> AsyncSession: ...


@dataclass(slots=True)
class SkillEvaluationWorker(SkillEvaluationQueue):
    evaluator: SkillEvaluationEvaluator = field(default_factory=LlmSkillEvaluationEvaluator)
    queue_max_size: int = field(default_factory=lambda: settings.skill_evaluation_queue_max_size)
    max_concurrent: int = field(default_factory=lambda: settings.skill_evaluation_max_concurrent)
    _session_factory: SkillEvaluationSessionFactory | None = field(default=None, init=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False)
    _active_tasks: set[asyncio.Task[None]] = field(default_factory=set, init=False)
    _semaphore: asyncio.Semaphore | None = field(default=None, init=False)
    _stopping: asyncio.Event | None = field(default=None, init=False)
    _holds_leader_lock: bool = field(default=False, init=False)

    async def start(
        self,
        session_factory: SkillEvaluationSessionFactory | None = None,
        *,
        reconcile_stale: bool = False,
    ) -> bool:
        if session_factory is not None:
            self._session_factory = session_factory
        if self._task is not None and not self._task.done():
            return True
        owns_database = self._session_factory is None or self._session_factory is async_session
        if owns_database and not await try_acquire_skill_evaluation_worker_leader():
            return False
        self._holds_leader_lock = owns_database
        if reconcile_stale:
            await self.reconcile_stale_runs(self._session_factory)
        self._stopping = asyncio.Event()
        self._wake = asyncio.Event()
        self._semaphore = asyncio.Semaphore(max(1, self.max_concurrent))
        self._task = asyncio.create_task(self._loop(), name="skill-evaluation-worker")
        return True

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
            for active_task in self._active_tasks:
                active_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            if self._active_tasks:
                await asyncio.gather(*self._active_tasks, return_exceptions=True)
        self._task = None
        self._active_tasks.clear()
        self._semaphore = None
        self._stopping = None
        self._wake = None
        if self._holds_leader_lock:
            self._holds_leader_lock = False
            await release_skill_evaluation_worker_leader()

    async def reconcile_stale_runs(
        self,
        session_factory: SkillEvaluationSessionFactory | None = None,
    ) -> int:
        """8.9: list companies with interrupted runs, then fail them per company."""
        return await reconcile_interrupted_by_company(session_factory or self._session_factory)

    async def run_once(self, db: AsyncSession, run_id: uuid.UUID) -> SkillEvaluationRun | None:
        run = await load_run(db, run_id)
        if run is None:
            return None
        if run.status == "cancelled":
            return run
        if run.status != "queued":
            raise SkillEvaluationExecutionError(f"run is not queued: {run.status}")

        try:
            context = await build_context(db, run)
        except SkillEvaluationExecutionError as exc:
            await mark_execution_error_failed(db, run, exc)
            return run
        await mark_running(db, run)
        await record_run_audit(db, run, "skill_evaluation.run_start")
        await mark_grading(db, run)
        await db.commit()
        await db.refresh(run)
        # Scale the whole-run timeout to the case count × arms so a legal
        # multi-case run isn't killed mid-run by a fixed cap (spec §4.1).
        run_timeout = effective_run_timeout_seconds(
            len(context.evals), uses_baseline_comparison=context.baseline_comparison
        )
        try:
            result = await asyncio.wait_for(
                self.evaluator.evaluate(db, context),
                timeout=run_timeout,
            )
        except EvalRunCancelled:
            await mark_cancelled(db, run)
            await db.flush()
            return run
        except TimeoutError:
            await mark_timeout_failed(db, run, timeout_seconds=run_timeout)
            return run
        except SkillEvaluationExecutionError as exc:
            await mark_execution_error_failed(db, run, exc)
            return run
        except ValueError as exc:
            await mark_execution_error_failed(db, run, SkillEvaluationExecutionError(str(exc)))
            return run

        if await cancel_if_requested(db, run):
            return run

        if not await mark_completed(db, run, result):
            if await cancel_if_requested(db, run):
                return run
            raise SkillEvaluationExecutionError(f"run completion conflicted: {run.status}")
        await record_completed_audit(db, run, result)
        await db.flush()
        # NOTE: the skill-axis usage ledger is written AFTER the outer commit
        # (see _record_usage_ledger_after_commit) so a cancellation during the
        # ledger write can never roll back this already-completed run.
        return run

    @staticmethod
    async def _record_usage_ledger_after_commit(
        run: SkillEvaluationRun, tenant_id: uuid.UUID | None
    ) -> None:
        """Best-effort skill-axis usage event for a COMMITTED completed run.

        Runs in its own session after the completion is durable — the run's FK
        target already exists, and CancelledError here can't unwind the run.
        """

        if run.status != "completed":
            return
        usage = run.usage
        if not isinstance(usage, dict):
            return
        await record_evaluation_usage_nonfatal(
            tenant_id=tenant_id,
            skill_id=run.skill_id,
            user_id=run.user_id,
            evaluation_run_id=run.id,
            model_name=run.runner_model,
            usage=usage,
        )

    async def mark_interrupted_runs(self, db: AsyncSession) -> int:
        return await mark_interrupted_runs(db)

    async def _loop(self) -> None:
        while self._stopping is None or not self._stopping.is_set():
            self._active_tasks = {task for task in self._active_tasks if not task.done()}
            if len(self._active_tasks) >= max(1, self.max_concurrent):
                await asyncio.wait(self._active_tasks, return_when=asyncio.FIRST_COMPLETED)
                continue
            item = self.pop_next()
            if item is None:
                wake = self._wake
                if wake is None:
                    return
                await wake.wait()
                wake.clear()
                continue
            task = asyncio.create_task(self._execute_run(item))
            # H-06 fair share: released when the task ends, also if cancelled before it ran.
            task.add_done_callback(lambda _done, popped=item: self.finish(popped))
            self._active_tasks.add(task)
        if self._active_tasks:
            await asyncio.gather(*self._active_tasks, return_exceptions=True)

    async def _execute_run(self, item: SkillEvaluationQueueItem) -> None:
        semaphore = self._semaphore
        if semaphore is not None:
            async with semaphore:
                await self._execute_run_with_session(item)
            return
        await self._execute_run_with_session(item)

    async def _execute_run_with_session(self, item: SkillEvaluationQueueItem) -> None:
        """One queue item, one company session (8.8): items never share a session."""
        run_id = item.run_id
        run: SkillEvaluationRun | None = None
        async with company_session(item.tenant_id, item.user_id, maker=self._session_factory) as db:
            try:
                run = await self.run_once(db, run_id)
            except SkillEvaluationExecutionError as exc:
                await db.rollback()
                logger.warning("skill evaluation run skipped run_id=%s error=%s", run_id, exc)
                return
            await db.commit()
        # Ledger write is deferred to AFTER the durable commit above so a late
        # cancellation can't roll back the completed run (Phase 3 §5.1).
        if run is not None:
            await self._record_usage_ledger_after_commit(run, item.tenant_id)


skill_evaluation_worker = SkillEvaluationWorker()

__all__ = ["SkillEvaluationQueueFull", "SkillEvaluationWorker", "skill_evaluation_worker"]
