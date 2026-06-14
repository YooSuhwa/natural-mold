from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.eval_cancellation import (
    EvalCancellationCheckpoint,
    EvalCancellationPhase,
)
from app.config import settings
from app.models.skill_evaluation import SkillEvaluationRun
from app.services import skill_evaluation_service
from app.services.skill_evaluation_worker import SkillEvaluationWorker
from app.services.skill_evaluation_worker_types import (
    SkillEvaluationContext,
    SkillEvaluationResult,
)
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID, TestSession

pytestmark = pytest.mark.asyncio


class BlockingEvaluator:
    def __init__(self) -> None:
        self.started: asyncio.Queue[uuid.UUID] = asyncio.Queue()
        self.release = asyncio.Event()

    async def evaluate(self, context: SkillEvaluationContext) -> SkillEvaluationResult:
        await self.started.put(context.run_id)
        await self.release.wait()
        return SkillEvaluationResult(
            summary={"case_count": len(context.evals), "pass_rate": 1},
            benchmark={"with_skill_pass_rate": 1, "without_skill_pass_rate": 0},
            case_results=[],
        )


class CheckpointBlockingEvaluator:
    def __init__(self) -> None:
        self.started: asyncio.Queue[uuid.UUID] = asyncio.Queue()
        self.release = asyncio.Event()

    async def evaluate(self, context: SkillEvaluationContext) -> SkillEvaluationResult:
        await self.started.put(context.run_id)
        await self.release.wait()
        await context.cancellation.raise_if_cancelled(
            EvalCancellationCheckpoint(EvalCancellationPhase.GRADING)
        )
        return SkillEvaluationResult(
            summary={"case_count": len(context.evals), "pass_rate": 1},
            benchmark={"with_skill_pass_rate": 1, "without_skill_pass_rate": 0},
            case_results=[],
        )


def _skill_content() -> str:
    return (
        "---\n"
        "name: evaluator\n"
        'description: "Use when testing skill evaluation cancellation."\n'
        "---\n\n"
        "Use when testing evaluation cancellation.\n"
    )


async def _create_run(db: AsyncSession) -> SkillEvaluationRun:
    skill = await skill_service.create_text_skill(
        db,
        user_id=TEST_USER_ID,
        name="Evaluator",
        slug=f"evaluator-{uuid.uuid4().hex[:8]}",
        description="Use when testing skill evaluation cancellation.",
        content=_skill_content(),
        version="1.0.0",
    )
    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        name="Cancellation",
        evals=[{"input": "wait"}],
    )
    return await skill_evaluation_service.create_run(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        evaluation_set=evaluation_set,
    )


async def _wait_for_status(run_id: uuid.UUID, status: str) -> SkillEvaluationRun:
    for _ in range(40):
        async with TestSession() as session:
            row = await session.get(SkillEvaluationRun, run_id)
            if row is not None and row.status == status:
                return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"run did not reach status {status}")


async def test_worker_preserves_running_cancellation_after_evaluator_returns(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(settings, "data_root", str(tmp_path)):
        run = await _create_run(db)
        await db.commit()
        evaluator = BlockingEvaluator()
        worker = SkillEvaluationWorker(evaluator=evaluator)

        await worker.start(TestSession)
        try:
            worker.reserve_slot()
            worker.enqueue(run.id, reserved=True)
            started = await asyncio.wait_for(evaluator.started.get(), timeout=1)
            assert started == run.id

            async with TestSession() as session:
                row = await session.get(SkillEvaluationRun, run.id)
                assert row is not None
                assert row.status == "grading"
                await skill_evaluation_service.cancel_run(session, row, reason="user")
                await session.commit()

            evaluator.release.set()
            cancelled = await _wait_for_status(run.id, "cancelled")
        finally:
            await worker.stop()

    assert cancelled.cancellation_reason == "user"
    assert cancelled.completed_at is not None


async def test_worker_stops_when_evaluator_checkpoint_sees_cancelled_run(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(settings, "data_root", str(tmp_path)):
        run = await _create_run(db)
        await db.commit()
        evaluator = CheckpointBlockingEvaluator()
        worker = SkillEvaluationWorker(evaluator=evaluator)

        await worker.start(TestSession)
        try:
            worker.reserve_slot()
            worker.enqueue(run.id, reserved=True)
            started = await asyncio.wait_for(evaluator.started.get(), timeout=1)
            assert started == run.id

            async with TestSession() as session:
                row = await session.get(SkillEvaluationRun, run.id)
                assert row is not None
                assert row.status == "grading"
                await skill_evaluation_service.cancel_run(session, row, reason="user")
                await session.commit()

            evaluator.release.set()
            cancelled = await _wait_for_status(run.id, "cancelled")
        finally:
            await worker.stop()

    assert cancelled.summary is None
    assert cancelled.error_message is None
    assert cancelled.cancellation_reason == "user"
