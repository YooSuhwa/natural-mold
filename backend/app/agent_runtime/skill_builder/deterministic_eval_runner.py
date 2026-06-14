from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.agent_runtime.skill_builder.eval_cancellation import (
    EvalCancellationCheckpoint,
    EvalCancellationPhase,
    EvalCancellationProbe,
)
from app.agent_runtime.skill_builder.eval_runner import (
    EvalCaseResult,
    aggregate_benchmark,
    validate_grader_result,
)
from app.schemas.skill_builder import JsonValue


@dataclass(frozen=True, slots=True)
class DeterministicEvaluationPayload:
    summary: dict[str, JsonValue]
    benchmark: dict[str, JsonValue]
    case_results: list[JsonValue]


async def run_deterministic_evaluation(
    *,
    evals: Sequence[JsonValue],
    runner_version: str,
    cancellation: EvalCancellationProbe,
) -> DeterministicEvaluationPayload:
    await cancellation.raise_if_cancelled(EvalCancellationCheckpoint(EvalCancellationPhase.START))
    with_skill_results = await _deterministic_with_skill_results(evals, cancellation)
    await cancellation.raise_if_cancelled(
        EvalCancellationCheckpoint(EvalCancellationPhase.SUBPROCESS_TIMEOUT)
    )
    without_skill_results = await _deterministic_baseline_results(evals, cancellation)
    await cancellation.raise_if_cancelled(EvalCancellationCheckpoint(EvalCancellationPhase.GRADING))
    case_results = _deterministic_case_results(evals)
    grader_result = validate_grader_result(
        _deterministic_grader_result(
            evals=evals,
            case_results=case_results,
            runner_version=runner_version,
        )
    )
    summary = dict(grader_result)
    case_count = len(case_results)
    summary["runner_version"] = runner_version
    summary["case_count"] = case_count
    summary["passed_count"] = case_count
    summary["failed_count"] = 0
    summary["pass_rate"] = 1 if case_count else 0
    await cancellation.raise_if_cancelled(
        EvalCancellationCheckpoint(EvalCancellationPhase.AGGREGATION)
    )
    return DeterministicEvaluationPayload(
        summary=summary,
        benchmark=aggregate_benchmark(
            with_skill=with_skill_results,
            without_skill=without_skill_results,
        ),
        case_results=case_results,
    )


async def _deterministic_with_skill_results(
    evals: Sequence[JsonValue],
    cancellation: EvalCancellationProbe,
) -> list[EvalCaseResult]:
    results: list[EvalCaseResult] = []
    for index, _case in enumerate(evals):
        await cancellation.raise_if_cancelled(
            EvalCancellationCheckpoint(EvalCancellationPhase.WITH_SKILL_CASE, case_index=index)
        )
        results.append(EvalCaseResult(case_index=index, passed=True, score=1))
    return results


async def _deterministic_baseline_results(
    evals: Sequence[JsonValue],
    cancellation: EvalCancellationProbe,
) -> list[EvalCaseResult]:
    results: list[EvalCaseResult] = []
    for index, _case in enumerate(evals):
        await cancellation.raise_if_cancelled(
            EvalCancellationCheckpoint(EvalCancellationPhase.BASELINE_CASE, case_index=index)
        )
        results.append(EvalCaseResult(case_index=index, passed=False, score=0))
    return results


def _deterministic_case_results(evals: Sequence[JsonValue]) -> list[JsonValue]:
    return [
        {
            "case_index": index,
            "status": "passed",
            "input": case,
            "score": 1,
            "notes": "Deterministic placeholder result.",
        }
        for index, case in enumerate(evals)
    ]


def _deterministic_grader_result(
    *,
    evals: Sequence[JsonValue],
    case_results: list[JsonValue],
    runner_version: str,
) -> dict[str, JsonValue]:
    case_count = len(case_results)
    return {
        "expectations": [case.get("expected") for case in evals if isinstance(case, dict)],
        "summary": {
            "runner_version": runner_version,
            "case_count": case_count,
            "passed_count": case_count,
            "failed_count": 0,
            "pass_rate": 1 if case_count else 0,
        },
        "execution_metrics": {
            "with_skill_runs": case_count,
            "without_skill_runs": case_count,
            "model_call_count": case_count * 3,
        },
        "timing": {
            "case_timeout_seconds": 0,
            "total_seconds": 0,
        },
        "claims": [
            {
                "case_index": index,
                "supported": True,
                "evidence": "Deterministic evaluator accepted the case.",
            }
            for index, _case in enumerate(evals)
        ],
        "eval_feedback": [
            {
                "case_index": index,
                "severity": "info",
                "message": "Deterministic placeholder result.",
            }
            for index, _case in enumerate(evals)
        ],
    }
