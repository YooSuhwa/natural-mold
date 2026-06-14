from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.models.skill_evaluation import SkillEvaluationRun
from app.schemas.skill_builder import JsonValue
from app.services import skill_evaluation_service
from app.services.skill_evaluation_worker import SkillEvaluationWorker
from app.services.skill_evaluation_worker_types import (
    SkillEvaluationContext,
    SkillEvaluationExecutionError,
    SkillEvaluationResult,
)
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID, TestSession

pytestmark = pytest.mark.asyncio


class FailingEvaluator:
    async def evaluate(self, context: SkillEvaluationContext) -> SkillEvaluationResult:
        raise SkillEvaluationExecutionError(f"runner unavailable for {context.run_id}")


class BlockingEvaluator:
    def __init__(self) -> None:
        self.started: asyncio.Queue[uuid.UUID] = asyncio.Queue()
        self.releases: dict[uuid.UUID, asyncio.Event] = {}

    async def evaluate(self, context: SkillEvaluationContext) -> SkillEvaluationResult:
        release = asyncio.Event()
        self.releases[context.run_id] = release
        await self.started.put(context.run_id)
        await release.wait()
        return SkillEvaluationResult(
            summary={"case_count": len(context.evals), "pass_rate": 1},
            benchmark={"with_skill_pass_rate": 1, "without_skill_pass_rate": 0},
            case_results=[],
        )


def _skill_content() -> str:
    return (
        "---\n"
        "name: evaluator\n"
        'description: "Use when testing skill evaluation behavior."\n'
        "---\n\n"
        "Use when testing evaluation behavior.\n"
    )


async def _create_run(
    db: AsyncSession,
    tmp_path: Path,
    *,
    evals: list[JsonValue] | None = None,
) -> SkillEvaluationRun:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Evaluator",
            slug=f"evaluator-{uuid.uuid4().hex[:8]}",
            description="Use when testing skill evaluation behavior.",
            content=_skill_content(),
            version="1.0.0",
        )
    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        name="Smoke",
        evals=evals or [{"input": "a"}],
    )
    return await skill_evaluation_service.create_run(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        evaluation_set=evaluation_set,
    )


async def _audit_actions(db: AsyncSession) -> list[str]:
    result = await db.execute(select(AuditEvent).order_by(AuditEvent.created_at))
    return [row.action for row in result.scalars().all()]


async def _wait_for_run_status(
    run_id: uuid.UUID,
    status: str,
) -> SkillEvaluationRun:
    for _ in range(20):
        async with TestSession() as session:
            row = await session.get(SkillEvaluationRun, run_id)
            if row is not None and row.status == status:
                return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"run did not reach status {status}")


async def test_worker_completes_queued_run_and_records_audit(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    run = await _create_run(db, tmp_path, evals=[{"input": "a"}, {"input": "b"}])
    worker = SkillEvaluationWorker()

    completed = await worker.run_once(db, run.id)
    await db.commit()

    assert completed is not None
    assert completed.status == "completed"
    assert completed.started_at is not None
    assert completed.completed_at is not None
    assert completed.summary is not None
    assert completed.summary["case_count"] == 2
    assert completed.summary["pass_rate"] == 1
    assert "expectations" in completed.summary
    assert "execution_metrics" in completed.summary
    assert "timing" in completed.summary
    assert "claims" in completed.summary
    assert "eval_feedback" in completed.summary
    assert completed.runner_version == "deterministic-1"
    assert completed.grader_prompt_version == "deterministic-grader-1"
    assert completed.eval_schema_version == 1
    assert completed.benchmark is not None
    assert completed.benchmark["with_skill_pass_rate"] == 1.0
    assert completed.benchmark["without_skill_pass_rate"] == 0.0
    assert completed.benchmark["pass_rate_delta"] == 1.0
    assert completed.benchmark["with_skill_min_score"] == 1
    assert completed.benchmark["without_skill_max_score"] == 0
    assert completed.case_results is not None
    assert len(completed.case_results) == 2
    assert await _audit_actions(db) == [
        "skill_evaluation.run_start",
        "skill_evaluation.run_complete",
    ]


async def test_worker_loop_consumes_enqueued_run(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    run = await _create_run(db, tmp_path, evals=[{"input": "queued"}])
    await db.commit()
    worker = SkillEvaluationWorker()

    await worker.start(TestSession)
    worker.reserve_slot()
    worker.enqueue(run.id, reserved=True)
    completed = await _wait_for_run_status(run.id, "completed")
    await worker.stop()

    assert completed.summary is not None
    assert completed.summary["case_count"] == 1


async def test_worker_loop_respects_max_concurrent(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    first = await _create_run(db, tmp_path, evals=[{"input": "first"}])
    second = await _create_run(db, tmp_path, evals=[{"input": "second"}])
    await db.commit()
    evaluator = BlockingEvaluator()
    worker = SkillEvaluationWorker(evaluator=evaluator, max_concurrent=1)

    await worker.start(TestSession)
    worker.reserve_slot()
    worker.enqueue(first.id, reserved=True)
    worker.reserve_slot()
    worker.enqueue(second.id, reserved=True)
    first_started = await asyncio.wait_for(evaluator.started.get(), timeout=1)
    await asyncio.sleep(0.1)
    async with TestSession() as session:
        second_row = await session.get(SkillEvaluationRun, second.id)
    assert first_started == first.id
    assert second_row is not None
    assert second_row.status == "queued"

    evaluator.releases[first.id].set()
    second_started = await asyncio.wait_for(evaluator.started.get(), timeout=1)
    evaluator.releases[second.id].set()
    completed = await _wait_for_run_status(second.id, "completed")
    await worker.stop()

    assert second_started == second.id
    assert completed.status == "completed"


async def test_worker_marks_failed_run_and_records_audit(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    run = await _create_run(db, tmp_path)
    worker = SkillEvaluationWorker(evaluator=FailingEvaluator())

    failed = await worker.run_once(db, run.id)
    await db.commit()

    assert failed is not None
    assert failed.status == "failed"
    assert failed.completed_at is not None
    assert failed.error_message is not None
    assert "runner unavailable" in failed.error_message
    assert await _audit_actions(db) == [
        "skill_evaluation.run_start",
        "skill_evaluation.run_fail",
    ]


async def test_worker_skips_cancelled_run(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    run = await _create_run(db, tmp_path)
    run.status = "cancelled"
    await db.flush()
    worker = SkillEvaluationWorker()

    skipped = await worker.run_once(db, run.id)

    assert skipped is run
    assert skipped.status == "cancelled"
    assert await _audit_actions(db) == []


async def test_worker_marks_interrupted_active_runs(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    running = await _create_run(db, tmp_path, evals=[{"input": "running"}])
    grading = await _create_run(db, tmp_path, evals=[{"input": "grading"}])
    running.status = "running"
    grading.status = "grading"
    await db.flush()
    worker = SkillEvaluationWorker()

    count = await worker.mark_interrupted_runs(db)
    await db.commit()

    assert count == 2
    assert running.status == "failed"
    assert grading.status == "failed"
    assert running.error_message == "interrupted: process restarted"
    assert grading.error_message == "interrupted: process restarted"
    assert await _audit_actions(db) == [
        "skill_evaluation.run_fail",
        "skill_evaluation.run_fail",
    ]
