from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.audit_event import AuditEvent
from app.models.credential_audit_log import CredentialAuditLog
from app.models.skill_evaluation import SkillEvaluationRun
from app.services.skill_evaluation_worker import SkillEvaluationWorker
from app.services.skill_evaluation_worker_types import DeterministicSkillEvaluationEvaluator
from tests.conftest import TestSession
from tests.skill_evaluation_worker_helpers import (
    BlockingEvaluator,
    FailingEvaluator,
    HangingEvaluator,
    audit_actions,
    create_run,
    create_script_run,
    wait_for_run_status,
)

pytestmark = pytest.mark.asyncio


async def test_worker_completes_queued_run_and_records_audit(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    run = await create_run(db, tmp_path, evals=[{"input": "a"}, {"input": "b"}])
    worker = SkillEvaluationWorker(evaluator=DeterministicSkillEvaluationEvaluator())

    with patch.object(settings, "data_root", str(tmp_path)):
        completed = await worker.run_once(db, run.id)
    await db.commit()

    assert completed is not None
    assert completed.status == "completed"
    assert completed.started_at is not None and completed.completed_at is not None
    assert completed.summary is not None
    assert completed.summary["case_count"] == 2
    assert completed.summary["pass_rate"] == 1
    for key in ("expectations", "execution_metrics", "timing", "claims", "eval_feedback"):
        assert key in completed.summary
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
    assert (tmp_path / "skill-evaluation-runs" / str(run.id) / "eval-policy-probe.txt").read_text(
        encoding="utf-8"
    ) == "ok"
    assert await audit_actions(db) == [
        "skill_evaluation.run_start",
        "skill_evaluation.run_complete",
    ]


async def test_worker_script_eval_uses_execute_in_skill_env_and_redaction(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.agent_runtime.skill_executor_audit.async_session", TestSession)
    run = await create_script_run(
        db,
        tmp_path,
        script=(
            "import os\n"
            "from pathlib import Path\n"
            "print('HOME=' + os.environ['HOME'])\n"
            "print('PYTHONPATH=' + os.environ['PYTHONPATH'])\n"
            "print('SKILL_OUTPUT_DIR=' + os.environ['SKILL_OUTPUT_DIR'])\n"
            "print('OUTPUTS_DIR=' + os.environ['OUTPUTS_DIR'])\n"
            "print('SECRET=' + os.environ['OPENAI_API_KEY'])\n"
            "Path(os.environ['SKILL_OUTPUT_DIR'], 'marker.txt').write_text('ok')\n"
        ),
        command="python scripts/probe.py",
        credential_secret="sk-eval-runtime-secret",
    )
    await db.commit()
    worker = SkillEvaluationWorker(evaluator=DeterministicSkillEvaluationEvaluator())

    with patch.object(settings, "data_root", str(tmp_path)):
        completed = await worker.run_once(db, run.id)
    await db.commit()

    assert completed is not None
    assert completed.status == "completed"
    assert completed.summary is not None
    assert completed.summary["passed_count"] == 1
    assert completed.summary["pass_rate"] == 1
    assert completed.case_results is not None
    execution = completed.case_results[0]["execution"]
    assert execution["status"] == "passed"
    preview = execution["output_preview"]
    assert "HOME=" in preview
    assert "PYTHONPATH=" in preview
    assert "SKILL_OUTPUT_DIR=" in preview
    assert "OUTPUTS_DIR=" in preview
    assert "OUTPUT_FILES: marker.txt" in preview
    assert "sk-eval-runtime-secret" not in preview
    assert "<redacted:OPENAI_API_KEY>" in preview
    assert (tmp_path / "skill-evaluation-runs" / str(run.id) / "marker.txt").read_text() == "ok"

    result = await db.execute(
        select(CredentialAuditLog).where(CredentialAuditLog.action == "invoke")
    )
    audit = result.scalar_one()
    assert audit.log_metadata is not None
    assert audit.log_metadata["kind"] == "skill_evaluation"
    assert audit.log_metadata["run_id"] == str(run.id)
    assert "sk-eval-runtime-secret" not in str(audit.log_metadata)


async def test_worker_script_eval_uses_execute_in_skill_sandbox_denial(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.agent_runtime.skill_executor_audit.async_session", TestSession)
    run = await create_script_run(
        db,
        tmp_path,
        script="print('should not run')\n",
        command="bash scripts/probe.py --token raw-secret",
    )
    await db.commit()
    worker = SkillEvaluationWorker(evaluator=DeterministicSkillEvaluationEvaluator())

    with patch.object(settings, "data_root", str(tmp_path)):
        completed = await worker.run_once(db, run.id)
    await db.commit()

    assert completed is not None
    assert completed.status == "completed"
    assert completed.summary is not None
    assert completed.summary["passed_count"] == 0
    assert completed.summary["failed_count"] == 1
    assert completed.case_results is not None
    execution = completed.case_results[0]["execution"]
    assert execution["status"] == "failed"
    assert execution["output_preview"] == "Error: only python, node, or curl commands are allowed."

    result = await db.execute(
        select(AuditEvent).where(AuditEvent.action == "skill_security.sandbox_denied")
    )
    event = result.scalar_one()
    assert event.reason_code == "unsupported_executable"
    assert event.run_id == str(run.id)
    assert event.event_metadata is not None
    assert event.event_metadata["kind"] == "skill_evaluation"
    assert event.event_metadata["command_executable"] == "bash"
    assert "raw-secret" not in str(event.event_metadata)


async def test_worker_loop_consumes_enqueued_run(db: AsyncSession, tmp_path: Path) -> None:
    run = await create_run(db, tmp_path, evals=[{"input": "queued"}])
    await db.commit()
    worker = SkillEvaluationWorker(evaluator=DeterministicSkillEvaluationEvaluator())

    with patch.object(settings, "data_root", str(tmp_path)):
        await worker.start(TestSession)
        worker.reserve_slot()
        worker.enqueue(run.id, reserved=True)
        completed = await wait_for_run_status(run.id, "completed")
        await worker.stop()

    assert completed.summary is not None
    assert completed.summary["case_count"] == 1


async def test_worker_loop_respects_max_concurrent(db: AsyncSession, tmp_path: Path) -> None:
    first = await create_run(db, tmp_path, evals=[{"input": "first"}])
    second = await create_run(db, tmp_path, evals=[{"input": "second"}])
    await db.commit()
    evaluator = BlockingEvaluator()
    worker = SkillEvaluationWorker(evaluator=evaluator, max_concurrent=1)

    try:
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
        assert second_started == second.id
        evaluator.releases[second.id].set()
        await worker.stop()
        completed = await wait_for_run_status(second.id, "completed")
    finally:
        await worker.stop()

    assert completed.status == "completed"


async def test_worker_marks_failed_run_and_records_audit(db: AsyncSession, tmp_path: Path) -> None:
    run = await create_run(db, tmp_path)
    worker = SkillEvaluationWorker(evaluator=FailingEvaluator())

    failed = await worker.run_once(db, run.id)
    await db.commit()

    assert failed is not None
    assert failed.status == "failed"
    assert failed.completed_at is not None
    assert failed.error_message is not None
    assert "runner unavailable" in failed.error_message
    assert await audit_actions(db) == ["skill_evaluation.run_start", "skill_evaluation.run_fail"]


async def test_worker_marks_timed_out_run_failed_and_records_sanitized_audit(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    run = await create_run(db, tmp_path)
    worker = SkillEvaluationWorker(evaluator=HangingEvaluator())

    with patch.object(settings, "skill_evaluation_run_timeout_seconds", 0.01):
        failed = await worker.run_once(db, run.id)
    await db.commit()

    assert failed is not None
    assert failed.status == "failed"
    assert failed.error_message == "timeout: evaluation run exceeded 0.01s"
    assert await audit_actions(db) == ["skill_evaluation.run_start", "skill_evaluation.run_fail"]
    result = await db.execute(
        select(AuditEvent).where(AuditEvent.action == "skill_evaluation.run_fail")
    )
    event = result.scalar_one()
    assert event.event_metadata is not None
    assert event.event_metadata["reason_code"] == "SKILL_EVALUATION_TIMEOUT"
    assert "timeout:" not in str(event.event_metadata)


async def test_worker_skips_cancelled_run(db: AsyncSession, tmp_path: Path) -> None:
    run = await create_run(db, tmp_path)
    run.status = "cancelled"
    await db.flush()
    worker = SkillEvaluationWorker(evaluator=DeterministicSkillEvaluationEvaluator())

    skipped = await worker.run_once(db, run.id)

    assert skipped is run
    assert skipped.status == "cancelled"
    assert await audit_actions(db) == []


async def test_worker_marks_interrupted_active_runs(db: AsyncSession, tmp_path: Path) -> None:
    running = await create_run(db, tmp_path, evals=[{"input": "running"}])
    grading = await create_run(db, tmp_path, evals=[{"input": "grading"}])
    running.status = "running"
    grading.status = "grading"
    await db.flush()
    worker = SkillEvaluationWorker(evaluator=DeterministicSkillEvaluationEvaluator())

    count = await worker.mark_interrupted_runs(db)
    await db.commit()

    assert count == 2
    assert running.status == "failed"
    assert grading.status == "failed"
    assert running.error_message == "interrupted: process restarted"
    assert grading.error_message == "interrupted: process restarted"
    assert await audit_actions(db) == ["skill_evaluation.run_fail", "skill_evaluation.run_fail"]
