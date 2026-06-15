from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.audit_event import AuditEvent
from app.models.skill_evaluation import SkillEvaluationRun
from app.services import skill_evaluation_service
from app.services.skill_evaluation_worker import SkillEvaluationWorker
from app.services.skill_evaluation_worker_types import (
    DeterministicSkillEvaluationEvaluator,
    SkillEvaluationContext,
    SkillEvaluationExecutionError,
    SkillEvaluationResult,
)
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio


class LeakyFailingEvaluator:
    async def evaluate(
        self,
        _db: AsyncSession,
        context: SkillEvaluationContext,
    ) -> SkillEvaluationResult:
        raise SkillEvaluationExecutionError("runner failed for prompt-secret and output-secret")


def _skill_content() -> str:
    return (
        "---\n"
        "name: evaluator\n"
        'description: "Use when testing skill evaluation audit metadata."\n'
        "---\n\n"
        "Use when testing evaluation audit metadata.\n"
    )


async def _create_run(db: AsyncSession) -> SkillEvaluationRun:
    skill = await skill_service.create_text_skill(
        db,
        user_id=TEST_USER_ID,
        name="Evaluator",
        slug=f"evaluator-{uuid.uuid4().hex[:8]}",
        description="Use when testing skill evaluation audit metadata.",
        content=_skill_content(),
        version="1.0.0",
    )
    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        name="Audit",
        evals=[{"input": "prompt-secret", "expected": "output-secret"}],
    )
    return await skill_evaluation_service.create_run(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        evaluation_set=evaluation_set,
    )


async def _audit_events(db: AsyncSession) -> list[AuditEvent]:
    result = await db.execute(select(AuditEvent).order_by(AuditEvent.created_at))
    return [
        event for event in result.scalars().all() if event.action.startswith("skill_evaluation.")
    ]


async def test_worker_complete_audit_has_summary_metrics_without_prompts(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(settings, "data_root", str(tmp_path)):
        run = await _create_run(db)
        completed = await SkillEvaluationWorker(
            evaluator=DeterministicSkillEvaluationEvaluator()
        ).run_once(db, run.id)
        await db.commit()

    events = await _audit_events(db)
    complete = next(event for event in events if event.action == "skill_evaluation.run_complete")

    assert completed is not None
    assert complete.event_metadata is not None
    assert complete.event_metadata["skill_id"] == str(run.skill_id)
    assert complete.event_metadata["evaluation_set_id"] == str(run.evaluation_set_id)
    assert complete.event_metadata["case_count"] == 1
    assert complete.event_metadata["pass_rate"] == 1
    assert "prompt-secret" not in str(complete.event_metadata)
    assert "output-secret" not in str(complete.event_metadata)


async def test_worker_failure_audit_uses_reason_code_without_raw_error(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(settings, "data_root", str(tmp_path)):
        run = await _create_run(db)
        failed = await SkillEvaluationWorker(evaluator=LeakyFailingEvaluator()).run_once(db, run.id)
        await db.commit()

    events = await _audit_events(db)
    failure = next(event for event in events if event.action == "skill_evaluation.run_fail")

    assert failed is not None
    assert failed.error_message is not None
    assert "prompt-secret" in failed.error_message
    assert failure.event_metadata is not None
    assert failure.event_metadata["skill_id"] == str(run.skill_id)
    assert failure.event_metadata["evaluation_set_id"] == str(run.evaluation_set_id)
    assert failure.event_metadata["reason_code"] == "SKILL_EVALUATION_EXECUTION_ERROR"
    assert "error_message" not in failure.event_metadata
    assert "prompt-secret" not in str(failure.event_metadata)
    assert "output-secret" not in str(failure.event_metadata)
