"""LLM 스킬 평가 러너 ``llm-2`` — 실측 with/without A/B (Phase 3 §4, D1).

케이스마다 최대 3회의 싱글턴 모델 콜을 수행한다:

1. with-arm — 스킬 페이로드(+실행 케이스면 샌드박스 실행 결과)와 함께 과제 해결
2. without-arm — 스킬 컨텍스트 없이 같은 과제 해결 (baseline **실측**)
3. grader — 두 실제 산출물을 expected에 대해 채점

legacy ``llm-1``은 grader가 baseline을 "추정"했다 — llm-2의 baseline 수치는
전부 실행 산출물 채점이다. arm별 wall-clock/usage_metadata가 케이스 행의
``duration_ms``/``tokens``/``baseline_*`` 슬롯을 채워 benchmark/kpi 델타가
실측으로 계산된다.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.agent import (
    SkillBuilderChatModel,
    build_skill_builder_chat_model,
)
from app.agent_runtime.skill_builder.deterministic_eval_execution import (
    deterministic_with_skill_results,
    has_execution_cases,
)
from app.agent_runtime.skill_builder.eval_cancellation import (
    EvalCancellationCheckpoint,
    EvalCancellationPhase,
)
from app.agent_runtime.skill_builder.eval_runner import (
    aggregate_benchmark,
    run_eval_runtime_policy_probe,
)
from app.schemas.skill_builder import JsonValue
from app.services.skill_evaluation_ab_arms import (
    AB_GRADER_SYSTEM_PROMPT,
    WITH_ARM_SYSTEM_PROMPT,
    WITHOUT_ARM_SYSTEM_PROMPT,
    CaseArmMeasurement,
    grader_user_content,
    measured_benchmark_extras,
    run_arm,
    with_arm_user_content,
    without_arm_user_content,
)
from app.services.skill_evaluation_llm_payload import (
    json_object_from_text,
    skill_payload,
)
from app.services.skill_evaluation_llm_results import (
    normalize_case_results,
    scores_from_case_results,
    summary_payload,
)
from app.services.skill_evaluation_result_schema import normalize_skill_evaluation_result
from app.services.skill_evaluation_usage import LlmUsageCollector, resolve_model_pricing
from app.services.skill_evaluation_worker_types import (
    SkillEvaluationContext,
    SkillEvaluationExecutionError,
    SkillEvaluationResult,
)

type ModelBuilder = Callable[[AsyncSession], Awaitable[SkillBuilderChatModel]]
type JsonObject = dict[str, JsonValue]

LLM_RUNNER_VERSION = "llm-2"
LLM_GRADER_PROMPT_VERSION = "llm-grader-2"


def _coerce_case_status(row: JsonObject, status_key: str, score_key: str) -> None:
    """Force a case status to a definite passed/failed before both the benchmark
    aggregate and the schema normalizer consume the raw row.

    The grader is asked for ``passed``/``failed`` only; any other value (a
    refusal, ``error``, prose) — or an ABSENT key — is resolved by score
    (>=0.5 → passed). Absent must also be resolved: the two downstream
    normalizers derive a missing status differently (one counts it as a failed
    baseline, the other drops it), so leaving it unset reintroduces a
    benchmark-vs-summary divergence. Only the caller decides *whether* to touch
    the baseline arm (skipped entirely when baseline is off).
    """

    if row.get(status_key) in ("passed", "failed"):
        return
    score = row.get(score_key)
    passed = (
        isinstance(score, int | float)
        and not isinstance(score, bool)
        and math.isfinite(score)  # inf/nan is garbage → not a pass
        and score >= 0.5
    )
    row[status_key] = "passed" if passed else "failed"


@dataclass(frozen=True, slots=True)
class LlmSkillEvaluationEvaluator:
    model_builder: ModelBuilder = build_skill_builder_chat_model
    runner_version: str = LLM_RUNNER_VERSION
    grader_prompt_version: str = LLM_GRADER_PROMPT_VERSION

    @classmethod
    def for_model(cls, model: BaseChatModel, *, model_name: str) -> LlmSkillEvaluationEvaluator:
        async def build_model(_db: AsyncSession) -> SkillBuilderChatModel:
            return SkillBuilderChatModel(model=model, model_name=model_name)

        return cls(model_builder=build_model)

    async def evaluate(
        self,
        db: AsyncSession,
        context: SkillEvaluationContext,
    ) -> SkillEvaluationResult:
        await context.cancellation.raise_if_cancelled(
            EvalCancellationCheckpoint(EvalCancellationPhase.START)
        )
        if not has_execution_cases(context.evals):
            await run_eval_runtime_policy_probe(context.runtime_context)
        _, execution_results = await deterministic_with_skill_results(
            context.evals,
            context.cancellation,
            context.runtime_context,
        )
        built_model = await self.model_builder(db)
        usage_collector = LlmUsageCollector()
        payload = skill_payload(context)
        baseline_enabled = context.baseline_comparison

        raw_rows: list[JsonValue] = []
        for index, case in enumerate(context.evals):
            raw_rows.append(
                await self._evaluate_case(
                    context=context,
                    model=built_model.model,
                    case=case,
                    case_index=index,
                    skill_payload=payload,
                    execution_result=execution_results.get(index),
                    baseline_enabled=baseline_enabled,
                    usage_collector=usage_collector,
                )
            )

        case_results = normalize_case_results(
            evals=context.evals,
            payload={"case_results": raw_rows},
            execution_results=execution_results,
        )
        await context.cancellation.raise_if_cancelled(
            EvalCancellationCheckpoint(EvalCancellationPhase.AGGREGATION)
        )
        summary = summary_payload(
            evals=context.evals,
            case_results=case_results,
            payload={},
            runner_version=self.runner_version,
            baseline_enabled=baseline_enabled,
        )
        benchmark = aggregate_benchmark(
            with_skill=scores_from_case_results(case_results, baseline=False),
            without_skill=(
                scores_from_case_results(case_results, baseline=True) if baseline_enabled else []
            ),
        )
        if not baseline_enabled:
            # Empty-arm aggregates read as "0% baseline" — a fake measurement.
            # Drop them so the schema layer reports an honest None instead.
            for key in list(benchmark):
                if key.startswith("without_") or key in ("pass_rate_delta", "mean_score_delta"):
                    benchmark.pop(key)
        benchmark.update(measured_benchmark_extras(baseline_enabled=baseline_enabled))
        summary, benchmark, case_results = normalize_skill_evaluation_result(
            evals=context.evals,
            raw_case_results=raw_rows,
            raw_summary=summary,
            raw_benchmark=benchmark,
        )
        pricing = await resolve_model_pricing(db, built_model.model_name)
        return SkillEvaluationResult(
            summary=summary,
            benchmark=benchmark,
            case_results=case_results,
            runner_model=built_model.model_name,
            runner_version=self.runner_version,
            grader_prompt_version=self.grader_prompt_version,
            usage=usage_collector.rollup(pricing),
        )

    @staticmethod
    async def _evaluate_case(
        *,
        context: SkillEvaluationContext,
        model: BaseChatModel,
        case: JsonValue,
        case_index: int,
        skill_payload: JsonObject,
        execution_result: JsonObject | None,
        baseline_enabled: bool,
        usage_collector: LlmUsageCollector,
    ) -> JsonObject:
        await context.cancellation.raise_if_cancelled(
            EvalCancellationCheckpoint(EvalCancellationPhase.WITH_SKILL_CASE, case_index=case_index)
        )
        with_run = await run_arm(
            model,
            system_prompt=WITH_ARM_SYSTEM_PROMPT,
            user_content=with_arm_user_content(
                case=case,
                skill_payload=skill_payload,
                execution_result=execution_result,
            ),
            collector=usage_collector,
        )
        without_run = None
        if baseline_enabled:
            await context.cancellation.raise_if_cancelled(
                EvalCancellationCheckpoint(
                    EvalCancellationPhase.BASELINE_CASE, case_index=case_index
                )
            )
            without_run = await run_arm(
                model,
                system_prompt=WITHOUT_ARM_SYSTEM_PROMPT,
                user_content=without_arm_user_content(case=case),
                collector=usage_collector,
            )
        measurement = CaseArmMeasurement(
            case_index=case_index,
            with_run=with_run,
            without_run=without_run,
        )
        await context.cancellation.raise_if_cancelled(
            EvalCancellationCheckpoint(EvalCancellationPhase.GRADING, case_index=case_index)
        )
        grader_run = await run_arm(
            model,
            system_prompt=AB_GRADER_SYSTEM_PROMPT,
            user_content=grader_user_content(
                case_index=case_index,
                case=case,
                measurement=measurement,
                execution_result=execution_result,
            ),
            collector=usage_collector,
        )
        # Per-case isolation — a single malformed grader answer must fail only
        # THIS case (marked "error"), never nuke the whole multi-case run.
        try:
            row = json_object_from_text(grader_run.answer)
        except SkillEvaluationExecutionError:
            row = {
                "status": "error",
                "score": 0,
                "grader_feedback": "Grader returned an unparseable response.",
                "evidence": "The evaluation grader did not return valid JSON for this case.",
            }
        # Positional identity — the grader's echoed index is advisory only.
        row["case_index"] = case_index
        # Both the benchmark aggregate (via scores_from_case_results._status) and
        # the final schema normalizer (via result_values.status) must agree on the
        # verdict — coerce any non-pass/fail status to a definite one up front so
        # the two paths cannot disagree (an "error"+0.9-score row otherwise reads
        # as 100% pass in the benchmark and 0% in the summary).
        _coerce_case_status(row, "status", "score")
        if not baseline_enabled:
            # Never keep a guessed baseline the run did not actually measure.
            row.pop("baseline_status", None)
            row.pop("baseline_score", None)
        else:
            _coerce_case_status(row, "baseline_status", "baseline_score")
        row.update(measurement.case_row_metrics())
        if execution_result is not None:
            row["execution"] = execution_result
        return row
