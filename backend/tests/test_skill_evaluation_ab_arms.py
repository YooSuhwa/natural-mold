"""Phase 3 §4 — llm-2 실측 A/B 러너 (with/without arm + grader)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import skill_evaluation_service
from app.services.skill_evaluation_llm import LlmSkillEvaluationEvaluator
from app.services.skill_evaluation_worker_state import build_context
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio


class FakeUsageChatModel(FakeListChatModel):
    """FakeListChatModel + per-call usage_metadata (input/output tokens)."""

    usage_per_call: tuple[int, int] = (100, 20)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        response = self.responses[self.i]
        if self.i < len(self.responses) - 1:
            self.i += 1
        tokens_in, tokens_out = self.usage_per_call
        message = AIMessage(
            content=response,
            usage_metadata={
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "total_tokens": tokens_in + tokens_out,
            },
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def _grader_json(*, passed: bool = True, baseline_passed: bool = False) -> str:
    return json.dumps(
        {
            "case_index": 0,
            "status": "passed" if passed else "failed",
            "score": 0.9 if passed else 0.2,
            "baseline_status": "passed" if baseline_passed else "failed",
            "baseline_score": 0.8 if baseline_passed else 0.1,
            "grader_feedback": "with-arm followed the skill; baseline missed the format.",
            "evidence": "The with-skill answer matches the expected table.",
        }
    )


async def _create_run(db: AsyncSession, tmp_path: Path, *, run_config: dict | None = None):
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="AB Evaluator",
            slug=f"ab-evaluator-{uuid.uuid4().hex[:8]}",
            description="Use when benchmarking with/without the skill.",
            content=(
                "---\n"
                "name: ab-evaluator\n"
                'description: "Use when benchmarking with/without the skill."\n'
                "---\n\nAlways answer with a table of owners and deadlines.\n"
            ),
            version="1.0.0",
        )
    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        name="AB smoke",
        evals=[{"input": "회의록에서 담당자와 마감일을 뽑아줘", "expected": "담당자/마감일 표"}],
    )
    return await skill_evaluation_service.create_run(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        evaluation_set=evaluation_set,
        run_config=run_config,
    )


async def test_two_arm_run_measures_real_baseline(db: AsyncSession, tmp_path: Path) -> None:
    run = await _create_run(db, tmp_path)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        context = await build_context(db, run)
    assert context.baseline_comparison is True

    model = FakeUsageChatModel(
        responses=[
            "with-arm answer: 담당자/마감일 표",  # 1. with-arm
            "without-arm answer: 일반 상식 답변",  # 2. without-arm
            _grader_json(),  # 3. grader
        ]
    )
    evaluator = LlmSkillEvaluationEvaluator.for_model(model, model_name="fake-ab-model")

    result = await evaluator.evaluate(db, context)

    assert result.runner_version == "llm-2"
    assert result.usage is not None
    assert result.usage["model_calls"] == 3
    assert result.usage["tokens_in"] == 300
    assert result.usage["tokens_out"] == 60

    row = result.case_results[0]
    # Real measured per-case metrics — arms actually ran.
    assert row["status"] == "passed"
    assert row["baseline_status"] == "failed"
    assert row["with_answer_preview"] == "with-arm answer: 담당자/마감일 표"
    assert row["without_answer_preview"] == "without-arm answer: 일반 상식 답변"
    assert row["tokens"] == 120
    assert row["baseline_tokens"] == 120
    assert isinstance(row["duration_ms"], int)
    assert isinstance(row["baseline_duration_ms"], int)

    benchmark = result.benchmark
    assert benchmark is not None
    assert benchmark["measured"] is True
    assert benchmark["baseline_skipped"] is False
    assert benchmark["with_skill_pass_rate"] == 1
    assert benchmark["without_skill_pass_rate"] == 0
    assert benchmark["pass_rate_delta"] == 1
    assert benchmark["token_delta"] == 0
    assert benchmark["comparison"]["tokens"]["with_skill"] == 120


async def test_baseline_comparison_off_skips_without_arm(db: AsyncSession, tmp_path: Path) -> None:
    run = await _create_run(db, tmp_path, run_config={"baseline_comparison": False})
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        context = await build_context(db, run)
    assert context.baseline_comparison is False

    model = FakeUsageChatModel(
        responses=[
            "with-arm answer",  # 1. with-arm
            _grader_json(),  # 2. grader (no without-arm call)
        ]
    )
    evaluator = LlmSkillEvaluationEvaluator.for_model(model, model_name="fake-ab-model")

    result = await evaluator.evaluate(db, context)

    assert result.usage is not None
    assert result.usage["model_calls"] == 2

    row = result.case_results[0]
    # The grader's baseline guess is dropped — nothing was measured.
    assert row["baseline_status"] is None
    assert row["baseline_score"] is None
    assert row["without_answer_preview"] is None
    assert row["baseline_tokens"] is None

    benchmark = result.benchmark
    assert benchmark is not None
    assert benchmark["measured"] is True
    assert benchmark["baseline_skipped"] is True
    assert benchmark["without_skill_pass_rate"] is None
    assert benchmark["pass_rate_delta"] is None

    # execution_metrics must NOT report phantom without-skill runs (review R1).
    metrics = result.summary["execution_metrics"]
    assert metrics["without_skill_runs"] == 0
    assert metrics["model_call_count"] == 2  # 1 case × (with-arm + grader)


async def test_baseline_off_sends_with_arm_not_baseline_prompt(
    db: AsyncSession, tmp_path: Path
) -> None:
    """review R3 — baseline-off must still send the WITH-skill arm as the solve
    call, never the without-skill prompt (a wrong-arm regression keeps 2 calls).
    """

    from app.services.skill_evaluation_ab_arms import (
        AB_GRADER_SYSTEM_PROMPT,
        WITH_ARM_SYSTEM_PROMPT,
        WITHOUT_ARM_SYSTEM_PROMPT,
    )

    seen_system_prompts: list[str] = []

    class RecordingModel(FakeUsageChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            seen_system_prompts.append(str(messages[0].content))
            return super()._generate(messages, stop, run_manager, **kwargs)

    run = await _create_run(db, tmp_path, run_config={"baseline_comparison": False})
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        context = await build_context(db, run)

    model = RecordingModel(responses=["with", _grader_json()])
    evaluator = LlmSkillEvaluationEvaluator.for_model(model, model_name="fake-ab-model")
    await evaluator.evaluate(db, context)

    assert seen_system_prompts == [WITH_ARM_SYSTEM_PROMPT, AB_GRADER_SYSTEM_PROMPT]
    assert WITHOUT_ARM_SYSTEM_PROMPT not in seen_system_prompts


async def test_malformed_grader_answer_isolates_to_one_case(
    db: AsyncSession, tmp_path: Path
) -> None:
    """review R3 — a single unparseable grader answer must fail only THAT case
    ('error'→failed), never nuke the whole multi-case run.
    """

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="AB Multi",
            slug=f"ab-multi-{uuid.uuid4().hex[:8]}",
            description="Use when testing per-case grader isolation.",
            content=(
                "---\nname: ab-multi\n"
                'description: "Use when testing per-case grader isolation."\n---\n\nBody.\n'
            ),
            version="1.0.0",
        )
    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        name="two cases",
        evals=[{"input": "case 0"}, {"input": "case 1"}],
    )
    run = await skill_evaluation_service.create_run(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        evaluation_set=evaluation_set,
        run_config={"baseline_comparison": False},
    )
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        context = await build_context(db, run)

    # case 0: with-arm + valid grader (pass). case 1: with-arm + GARBAGE grader.
    model = FakeUsageChatModel(
        responses=[
            "with-arm 0",
            _grader_json(),
            "with-arm 1",
            "I'm sorry, I can't grade this.",  # non-JSON grader → error, isolated
        ]
    )
    evaluator = LlmSkillEvaluationEvaluator.for_model(model, model_name="fake-ab-model")

    result = await evaluator.evaluate(db, context)  # must NOT raise

    assert len(result.case_results) == 2
    assert result.case_results[0]["status"] == "passed"
    assert result.case_results[1]["status"] == "failed"  # error coerced to failed
    # Benchmark and summary agree: 1/2 passed (no self-contradiction).
    assert result.summary["pass_rate"] == 0.5
    assert result.benchmark["with_skill_pass_rate"] == 0.5


async def test_non_pass_status_with_high_score_is_consistent(
    db: AsyncSession, tmp_path: Path
) -> None:
    """review R4 — a grader returning a non-{passed,failed} status WITH a high
    score must not read as 100% in the benchmark and 0% in the summary. The
    leaf coercion (score>=0.5 → passed) makes both normalizers agree.
    """

    run = await _create_run(db, tmp_path)  # baseline ON (default)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        context = await build_context(db, run)

    # Grader returns status='inconclusive' (not passed/failed) with score 0.9,
    # and OMITS baseline_status entirely — both are the divergence triggers.
    weird_grader = json.dumps(
        {
            "case_index": 0,
            "status": "inconclusive",
            "score": 0.9,
            "baseline_score": 0.1,  # no baseline_status key
            "grader_feedback": "unusual",
            "evidence": "e",
        }
    )
    model = FakeUsageChatModel(responses=["with", "without", weird_grader])
    evaluator = LlmSkillEvaluationEvaluator.for_model(model, model_name="fake-ab-model")

    result = await evaluator.evaluate(db, context)

    # score 0.9 → passed; benchmark and summary must BOTH read it as passed.
    assert result.case_results[0]["status"] == "passed"
    assert result.summary["pass_rate"] == 1.0
    assert result.benchmark["with_skill_pass_rate"] == 1.0
    # baseline_status was absent → coerced by baseline_score 0.1 → failed,
    # counted consistently (denominator 1, not dropped).
    assert result.case_results[0]["baseline_status"] == "failed"
    assert result.benchmark["without_skill_pass_rate"] == 0.0


async def test_scripted_model_answers_eval_arm_prompts() -> None:
    """E2E scripted 모델이 arm/grader 프롬프트에 결정론적으로 응답한다.

    ab_arms의 프롬프트 첫 줄과 scripted 모델의 리터럴 마커가 어긋나면(drift)
    라이브 E2E에서 평가 런이 조용히 fallback 텍스트를 받게 된다 — 여기서 잠근다.
    """

    from langchain_core.messages import HumanMessage, SystemMessage

    from app.agent_runtime.e2e_scripted_model import E2EScriptedChatModel
    from app.services.skill_evaluation_ab_arms import (
        AB_GRADER_SYSTEM_PROMPT,
        WITH_ARM_SYSTEM_PROMPT,
        WITHOUT_ARM_SYSTEM_PROMPT,
    )

    model = E2EScriptedChatModel()

    with_answer = await model.ainvoke(
        [SystemMessage(content=WITH_ARM_SYSTEM_PROMPT), HumanMessage(content="{}")]
    )
    assert "담당자/마감일" in str(with_answer.content)
    assert with_answer.usage_metadata is not None
    assert with_answer.usage_metadata["input_tokens"] > 0

    without_answer = await model.ainvoke(
        [SystemMessage(content=WITHOUT_ARM_SYSTEM_PROMPT), HumanMessage(content="{}")]
    )
    assert str(without_answer.content) != str(with_answer.content)

    grader_answer = await model.ainvoke(
        [SystemMessage(content=AB_GRADER_SYSTEM_PROMPT), HumanMessage(content="{}")]
    )
    grader_payload = json.loads(str(grader_answer.content))
    assert grader_payload["status"] == "passed"
    assert grader_payload["baseline_status"] == "failed"


async def test_arm_prompts_reach_model_in_order(db: AsyncSession, tmp_path: Path) -> None:
    """with → without → grader 순서로 각 arm 시스템 프롬프트가 전달된다."""

    from app.services.skill_evaluation_ab_arms import (
        AB_GRADER_SYSTEM_PROMPT,
        WITH_ARM_SYSTEM_PROMPT,
        WITHOUT_ARM_SYSTEM_PROMPT,
    )

    seen_system_prompts: list[str] = []

    class RecordingModel(FakeUsageChatModel):
        def _generate(
            self,
            messages: list[BaseMessage],
            stop: list[str] | None = None,
            run_manager: Any = None,
            **kwargs: Any,
        ) -> ChatResult:
            seen_system_prompts.append(str(messages[0].content))
            return super()._generate(messages, stop, run_manager, **kwargs)

    run = await _create_run(db, tmp_path)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        context = await build_context(db, run)

    model = RecordingModel(responses=["with", "without", _grader_json()])
    evaluator = LlmSkillEvaluationEvaluator.for_model(model, model_name="fake-ab-model")
    await evaluator.evaluate(db, context)

    assert seen_system_prompts == [
        WITH_ARM_SYSTEM_PROMPT,
        WITHOUT_ARM_SYSTEM_PROMPT,
        AB_GRADER_SYSTEM_PROMPT,
    ]
