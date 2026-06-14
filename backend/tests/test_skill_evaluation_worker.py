from __future__ import annotations

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
from app.services.skill_evaluation_worker import (
    SkillEvaluationContext,
    SkillEvaluationExecutionError,
    SkillEvaluationResult,
    SkillEvaluationWorker,
)
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio


class FailingEvaluator:
    async def evaluate(self, context: SkillEvaluationContext) -> SkillEvaluationResult:
        raise SkillEvaluationExecutionError(f"runner unavailable for {context.run_id}")


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
    assert completed.case_results is not None
    assert len(completed.case_results) == 2
    assert await _audit_actions(db) == [
        "skill_evaluation.run_start",
        "skill_evaluation.run_complete",
    ]


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
