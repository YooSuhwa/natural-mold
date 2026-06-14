from __future__ import annotations

import asyncio
import io
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.credentials import service as credential_service
from app.models.audit_event import AuditEvent
from app.models.credential_audit_log import CredentialAuditLog
from app.models.marketplace import SkillCredentialBinding
from app.models.skill_evaluation import SkillEvaluationRun
from app.models.user import User
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


class HangingEvaluator:
    async def evaluate(self, context: SkillEvaluationContext) -> SkillEvaluationResult:
        await asyncio.sleep(10)
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


def _package_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


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


async def _create_script_run(
    db: AsyncSession,
    tmp_path: Path,
    *,
    script: str,
    command: str,
    credential_secret: str | None = None,
) -> SkillEvaluationRun:
    db.add(
        User(
            id=TEST_USER_ID,
            email="skill-eval-worker@test.com",
            name="Skill Eval Worker",
            hashed_password="h",
            is_active=True,
            is_super_user=False,
        )
    )
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_package_skill(
            db,
            user_id=TEST_USER_ID,
            zip_bytes=_package_zip(
                {
                    "SKILL.md": _skill_content(),
                    "scripts/probe.py": script,
                }
            ),
            name_override="Evaluator Package",
        )
    if credential_secret is not None:
        skill.credential_requirements = [
            {
                "key": "openai",
                "definition_key": "openai",
                "required": True,
                "label": "OpenAI",
                "fields": ["api_key"],
                "env_map": {"api_key": "OPENAI_API_KEY"},
            }
        ]
        credential = await credential_service.create(
            db,
            user_id=TEST_USER_ID,
            definition_key="openai",
            name="eval key",
            data={"api_key": credential_secret},
        )
        db.add(
            SkillCredentialBinding(
                skill_id=skill.id,
                user_id=TEST_USER_ID,
                requirement_key="openai",
                credential_id=credential.id,
                scope="skill",
            )
        )
    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        name="Script smoke",
        evals=[
            {
                "input": "run the script-backed evaluation case",
                "expected": "script completes",
                "metadata": {"execute_in_skill": {"command": command}},
            }
        ],
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
    last_seen: str | None = None
    last_error: str | None = None
    for _ in range(40):
        async with TestSession() as session:
            row = await session.get(SkillEvaluationRun, run_id)
            if row is not None:
                last_seen = row.status
                last_error = row.error_message
            if row is not None and row.status == status:
                return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"run did not reach status {status}; last={last_seen}; error={last_error}")


async def test_worker_completes_queued_run_and_records_audit(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    run = await _create_run(db, tmp_path, evals=[{"input": "a"}, {"input": "b"}])
    worker = SkillEvaluationWorker()

    with patch.object(settings, "data_root", str(tmp_path)):
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
    assert (tmp_path / "skill-evaluation-runs" / str(run.id) / "eval-policy-probe.txt").read_text(
        encoding="utf-8"
    ) == "ok"
    assert await _audit_actions(db) == [
        "skill_evaluation.run_start",
        "skill_evaluation.run_complete",
    ]


async def test_worker_script_eval_uses_execute_in_skill_env_and_redaction(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.agent_runtime.skill_executor_audit.async_session", TestSession)
    run = await _create_script_run(
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
    worker = SkillEvaluationWorker()

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

    marker = tmp_path / "skill-evaluation-runs" / str(run.id) / "marker.txt"
    assert marker.read_text() == "ok"

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
    run = await _create_script_run(
        db,
        tmp_path,
        script="print('should not run')\n",
        command="bash scripts/probe.py --token raw-secret",
    )
    await db.commit()
    worker = SkillEvaluationWorker()

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


async def test_worker_loop_consumes_enqueued_run(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    run = await _create_run(db, tmp_path, evals=[{"input": "queued"}])
    await db.commit()
    worker = SkillEvaluationWorker()

    with patch.object(settings, "data_root", str(tmp_path)):
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
        completed = await _wait_for_run_status(second.id, "completed")
    finally:
        await worker.stop()

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


async def test_worker_marks_timed_out_run_failed_and_records_sanitized_audit(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    run = await _create_run(db, tmp_path)
    worker = SkillEvaluationWorker(evaluator=HangingEvaluator())

    with patch.object(settings, "skill_evaluation_run_timeout_seconds", 0.01):
        failed = await worker.run_once(db, run.id)
    await db.commit()

    assert failed is not None
    assert failed.status == "failed"
    assert failed.error_message == "timeout: evaluation run exceeded 0.01s"
    assert await _audit_actions(db) == [
        "skill_evaluation.run_start",
        "skill_evaluation.run_fail",
    ]
    result = await db.execute(
        select(AuditEvent).where(AuditEvent.action == "skill_evaluation.run_fail")
    )
    event = result.scalar_one()
    assert event.event_metadata is not None
    assert event.event_metadata["reason_code"] == "SKILL_EVALUATION_TIMEOUT"
    assert "timeout:" not in str(event.event_metadata)


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
