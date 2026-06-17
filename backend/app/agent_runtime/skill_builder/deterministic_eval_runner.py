from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.agent_runtime.skill_builder.deterministic_eval_execution import (
    deterministic_with_skill_results,
    has_execution_cases,
)
from app.agent_runtime.skill_builder.eval_cancellation import (
    EvalCancellationCheckpoint,
    EvalCancellationPhase,
    EvalCancellationProbe,
)
from app.agent_runtime.skill_builder.eval_runner import (
    EvalCaseResult,
    aggregate_benchmark,
    run_eval_runtime_policy_probe,
    validate_grader_result,
)
from app.marketplace.skill_runtime import SkillToolContext
from app.schemas.skill_builder import JsonValue
from app.services.skill_evaluation_result_schema import normalize_skill_evaluation_result


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
    runtime_context: SkillToolContext | None = None,
) -> DeterministicEvaluationPayload:
    await cancellation.raise_if_cancelled(EvalCancellationCheckpoint(EvalCancellationPhase.START))
    if runtime_context is not None and not has_execution_cases(evals):
        await run_eval_runtime_policy_probe(runtime_context)
    with_skill_results, execution_results = await deterministic_with_skill_results(
        evals,
        cancellation,
        runtime_context,
    )
    await cancellation.raise_if_cancelled(
        EvalCancellationCheckpoint(EvalCancellationPhase.SUBPROCESS_TIMEOUT)
    )
    without_skill_results = await _deterministic_baseline_results(evals, cancellation)
    await cancellation.raise_if_cancelled(EvalCancellationCheckpoint(EvalCancellationPhase.GRADING))
    case_results = _deterministic_case_results(with_skill_results, execution_results)
    grader_result = validate_grader_result(
        _deterministic_grader_result(
            evals=evals,
            case_results=case_results,
            runner_version=runner_version,
        )
    )
    summary = dict(grader_result)
    case_count = len(case_results)
    passed_count = _passed_count(case_results)
    summary["runner_version"] = runner_version
    summary["case_count"] = case_count
    summary["passed_count"] = passed_count
    summary["failed_count"] = case_count - passed_count
    summary["pass_rate"] = _pass_rate(case_results)
    benchmark = aggregate_benchmark(
        with_skill=with_skill_results,
        without_skill=without_skill_results,
    )
    summary, benchmark, case_results = normalize_skill_evaluation_result(
        evals=evals,
        raw_case_results=case_results,
        raw_summary=summary,
        raw_benchmark=benchmark,
    )
    await cancellation.raise_if_cancelled(
        EvalCancellationCheckpoint(EvalCancellationPhase.AGGREGATION)
    )
    return DeterministicEvaluationPayload(
        summary=summary,
        benchmark=benchmark,
        case_results=case_results,
    )


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


def _deterministic_case_results(
    results: list[EvalCaseResult],
    execution_results: dict[int, dict[str, JsonValue]],
) -> list[JsonValue]:
    rows: list[JsonValue] = []
    for result in results:
        row: dict[str, JsonValue] = {
            "case_index": result.case_index,
            "status": "passed" if result.passed else "failed",
            "score": result.score,
            "notes": (
                "Deterministic placeholder result."
                if result.case_index not in execution_results
                else "Executed through execute_in_skill."
            ),
        }
        execution = execution_results.get(result.case_index)
        if execution is not None:
            row["execution"] = execution
        rows.append(row)
    return rows


def _case_status(row: JsonValue) -> str:
    if isinstance(row, dict):
        status = row.get("status")
        if isinstance(status, str):
            return status
    return "failed"


def _case_evidence(row: JsonValue) -> str:
    if not isinstance(row, dict):
        return "Deterministic evaluator produced no structured result."
    execution = row.get("execution")
    if isinstance(execution, dict):
        return "Skill script execution completed through execute_in_skill."
    return "Deterministic evaluator accepted the case."


def _case_execution_count(case_results: list[JsonValue]) -> int:
    return sum(
        1
        for row in case_results
        if isinstance(row, dict) and isinstance(row.get("execution"), dict)
    )


def _passed_count(case_results: list[JsonValue]) -> int:
    return sum(1 for row in case_results if _case_status(row) == "passed")


def _case_count(case_results: list[JsonValue]) -> int:
    return len(case_results)


def _pass_rate(case_results: list[JsonValue]) -> float:
    count = _case_count(case_results)
    if count == 0:
        return 0
    return round(_passed_count(case_results) / count, 6)


def _deterministic_grader_result(
    *,
    evals: Sequence[JsonValue],
    case_results: list[JsonValue],
    runner_version: str,
) -> dict[str, JsonValue]:
    case_count = _case_count(case_results)
    passed_count = _passed_count(case_results)
    failed_count = case_count - passed_count
    return {
        "expectations": [case.get("expected") for case in evals if isinstance(case, dict)],
        "summary": {
            "runner_version": runner_version,
            "case_count": case_count,
            "passed_count": passed_count,
            "failed_count": failed_count,
            "pass_rate": _pass_rate(case_results),
        },
        "execution_metrics": {
            "with_skill_runs": case_count,
            "without_skill_runs": case_count,
            "model_call_count": case_count * 3,
            "tool_calls": _case_execution_count(case_results),
        },
        "timing": {
            "case_timeout_seconds": 0,
            "total_seconds": 0,
        },
        "claims": [
            {
                "case_index": index,
                "supported": _case_status(result) == "passed",
                "evidence": _case_evidence(result),
            }
            for index, result in enumerate(case_results)
        ],
        "eval_feedback": [
            {
                "case_index": index,
                "severity": "info" if _case_status(result) == "passed" else "warning",
                "message": (
                    "Deterministic placeholder result."
                    if not isinstance(result, dict) or "execution" not in result
                    else "Script-backed evaluation case executed through the skill sandbox."
                ),
            }
            for index, result in enumerate(case_results)
        ],
    }
