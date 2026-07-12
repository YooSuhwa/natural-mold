from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.skill_builder import JsonValue
from app.services import skill_evaluation_service
from app.services.skill_evaluation_llm import LlmSkillEvaluationEvaluator
from app.services.skill_evaluation_worker import SkillEvaluationWorker
from app.services.skill_evaluation_worker_state import build_context
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio


def _skill_content() -> str:
    return (
        "---\n"
        "name: llm-evaluator\n"
        'description: "Use when testing LLM skill evaluation."\n'
        "---\n\n"
        "Always answer with concise meeting action items.\n"
    )


async def test_default_worker_evaluator_is_llm_backed() -> None:
    worker = SkillEvaluationWorker()

    assert worker.evaluator.__class__.__name__ == "LlmSkillEvaluationEvaluator"


async def test_llm_evaluator_grades_cases_with_system_model(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    run = await _create_llm_run(db, tmp_path)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        context = await build_context(db, run)

    evaluator = LlmSkillEvaluationEvaluator.for_model(_fake_model(), model_name="fake-eval-model")
    result = await evaluator.evaluate(db, context)

    assert result.runner_model == "fake-eval-model"
    assert result.runner_version == "llm-2"
    assert result.grader_prompt_version == "llm-grader-2"
    assert result.summary["case_count"] == 1
    assert result.summary["passed_count"] == 1
    assert result.summary["pass_rate"] == 1
    assert result.summary["schema_version"] == 2
    assert result.summary["kpis"]["pass_rate"]["value"] == 1
    assert result.benchmark is not None
    assert result.benchmark["pass_rate_delta"] == 1
    assert result.benchmark["comparison"]["pass_rate"]["delta"] == 1
    assert result.case_results is not None
    assert result.case_results[0]["review_status"] == "unreviewed"
    assert (
        result.case_results[0]["grader_feedback"]
        == "SKILL.md gives the needed extraction behavior."
    )


async def test_worker_persists_llm_evaluation_result(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import skill_usage_service
    from tests.conftest import TestSession

    # The ledger write happens in its own session after the run commits.
    monkeypatch.setattr(skill_usage_service, "async_session", TestSession)

    run = await _create_llm_run(db, tmp_path)
    evaluator = LlmSkillEvaluationEvaluator.for_model(_fake_model(), model_name="fake-eval-model")
    worker = SkillEvaluationWorker(evaluator=evaluator)

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        completed = await worker.run_once(db, run.id)
    await db.commit()

    assert completed is not None
    assert completed.status == "completed"
    assert completed.runner_model == "fake-eval-model"
    assert completed.runner_version == "llm-2"
    assert completed.summary is not None
    assert completed.summary["pass_rate"] == 1
    assert completed.summary["schema_version"] == 2
    assert completed.case_results is not None
    assert completed.case_results[0]["baseline_status"] == "failed"

    # Phase 3 §5.1 — measured usage rollup persists on the run in-transaction.
    # FakeListChatModel reports no usage_metadata, so tokens stay 0 and cost
    # stays unknown. llm-2 makes 3 calls for the single case (with/without/grader).
    assert completed.usage is not None
    assert completed.usage["measured"] is True
    assert completed.usage["model_calls"] == 3
    assert completed.usage["cost_usd"] is None

    # The skill-axis ledger event is written after commit in its own session
    # (cancellation there can't roll back the completed run).
    await worker._record_usage_ledger_after_commit(completed)  # noqa: SLF001 — test seam

    from sqlalchemy import select

    from app.models.skill_usage_event import SkillUsageEvent

    events = (
        (
            await db.execute(
                select(SkillUsageEvent).where(SkillUsageEvent.evaluation_run_id == completed.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].source_kind == "evaluation_run"
    assert events[0].skill_id == completed.skill_id
    assert events[0].model_name == "fake-eval-model"
    assert events[0].cost_usd is None


async def test_llm_evaluator_sanitizes_non_finite_scores(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    run = await _create_llm_run(db, tmp_path)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        context = await build_context(db, run)

    evaluator = LlmSkillEvaluationEvaluator.for_model(
        _fake_model_with_payload(
            {
                "case_index": 0,
                "score": float("nan"),
                "baseline_score": float("inf"),
                "grader_feedback": "Bad numeric output.",
                "evidence": "The grader returned non-finite numbers.",
            }
        ),
        model_name="fake-eval-model",
    )

    result = await evaluator.evaluate(db, context)

    assert result.case_results is not None
    assert result.case_results[0]["score"] == 0.0
    # Non-finite score sanitizes to None, and a non-finite/absent status is
    # coerced to a DEFINITE 'failed' (inf/nan is not a pass) so the benchmark
    # and summary normalizers can't disagree (review R4). baseline_status is no
    # longer left None — it's a definite failed, consistently counted.
    assert result.case_results[0]["baseline_score"] is None
    assert result.case_results[0]["baseline_status"] == "failed"
    assert result.case_results[0]["status"] == "failed"
    assert result.benchmark is not None
    # No self-contradiction: both read the case as failed (0% with-skill).
    assert result.summary["pass_rate"] == 0.0
    assert result.benchmark["with_skill_pass_rate"] == 0.0
    json.dumps(result.summary, allow_nan=False)
    json.dumps(result.benchmark, allow_nan=False)
    json.dumps(result.case_results, allow_nan=False)


async def _create_llm_run(
    db: AsyncSession,
    tmp_path: Path,
):
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="LLM Evaluator",
            slug=f"llm-evaluator-{uuid.uuid4().hex[:8]}",
            description="Use when testing LLM skill evaluation.",
            content=_skill_content(),
            version="1.0.0",
        )
    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        name="LLM smoke",
        evals=[{"input": "회의록에서 담당자와 마감일을 뽑아줘", "expected": "담당자/마감일 표"}],
    )
    return await skill_evaluation_service.create_run(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        evaluation_set=evaluation_set,
    )


def _fake_model() -> FakeListChatModel:
    # llm-2 per-case grader shape — the same response also serves as the
    # with/without arm answers (FakeListChatModel repeats its last response).
    return _fake_model_with_payload(
        {
            "case_index": 0,
            "status": "passed",
            "score": 0.92,
            "baseline_status": "failed",
            "baseline_score": 0.15,
            "grader_feedback": "SKILL.md gives the needed extraction behavior.",
            "evidence": "The skill explicitly targets meeting action items.",
        }
    )


def _fake_model_with_payload(payload: dict[str, JsonValue]) -> FakeListChatModel:
    return FakeListChatModel(responses=[json.dumps(payload)])
