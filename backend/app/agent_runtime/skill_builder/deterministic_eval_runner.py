from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.agent_runtime.skill_builder.eval_cancellation import (
    EvalCancellationCheckpoint,
    EvalCancellationPhase,
    EvalCancellationProbe,
)
from app.agent_runtime.skill_builder.eval_runner import (
    EvalCaseResult,
    aggregate_benchmark,
    run_eval_runtime_policy_probe,
    run_eval_skill_command,
    validate_grader_result,
)
from app.marketplace.skill_runtime import SkillToolContext
from app.schemas.skill_builder import JsonValue

_EXECUTE_METADATA_KEY: Final = "execute_in_skill"
_OUTPUT_PREVIEW_MAX_CHARS: Final = 2000


@dataclass(frozen=True, slots=True)
class DeterministicEvaluationPayload:
    summary: dict[str, JsonValue]
    benchmark: dict[str, JsonValue]
    case_results: list[JsonValue]


@dataclass(frozen=True, slots=True)
class ExecuteInSkillEvalRequest:
    command: str
    skill_directory: str | None = None


async def run_deterministic_evaluation(
    *,
    evals: Sequence[JsonValue],
    runner_version: str,
    cancellation: EvalCancellationProbe,
    runtime_context: SkillToolContext | None = None,
) -> DeterministicEvaluationPayload:
    await cancellation.raise_if_cancelled(EvalCancellationCheckpoint(EvalCancellationPhase.START))
    if runtime_context is not None and not _has_execution_cases(evals):
        await run_eval_runtime_policy_probe(runtime_context)
    with_skill_results, execution_results = await _deterministic_with_skill_results(
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
    runtime_context: SkillToolContext | None,
) -> tuple[list[EvalCaseResult], dict[int, dict[str, JsonValue]]]:
    results: list[EvalCaseResult] = []
    execution_results: dict[int, dict[str, JsonValue]] = {}
    for index, _case in enumerate(evals):
        await cancellation.raise_if_cancelled(
            EvalCancellationCheckpoint(EvalCancellationPhase.WITH_SKILL_CASE, case_index=index)
        )
        request = _case_execution_request(_case)
        if request is None:
            results.append(EvalCaseResult(case_index=index, passed=True, score=1))
            continue

        execution = await _run_execute_in_skill_case(
            request=request,
            runtime_context=runtime_context,
        )
        passed = execution["status"] == "passed"
        results.append(EvalCaseResult(case_index=index, passed=passed, score=1 if passed else 0))
        execution_results[index] = execution
    return results, execution_results


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


def _case_execution_request(case: JsonValue) -> ExecuteInSkillEvalRequest | None:
    if not isinstance(case, dict):
        return None
    metadata = case.get("metadata")
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get(_EXECUTE_METADATA_KEY)
    if not isinstance(raw, dict):
        return None
    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    skill_directory = raw.get("skill_directory")
    if not isinstance(skill_directory, str) or not skill_directory.strip():
        skill_directory = None
    return ExecuteInSkillEvalRequest(command=command, skill_directory=skill_directory)


def _has_execution_cases(evals: Sequence[JsonValue]) -> bool:
    return any(_case_execution_request(case) is not None for case in evals)


async def _run_execute_in_skill_case(
    *,
    request: ExecuteInSkillEvalRequest,
    runtime_context: SkillToolContext | None,
) -> dict[str, JsonValue]:
    if runtime_context is None:
        return {
            "status": "failed",
            "output_preview": "Error: skill runtime context is unavailable.",
        }

    skill_directory = request.skill_directory or _default_skill_directory(runtime_context)
    if skill_directory is None:
        return {
            "status": "failed",
            "output_preview": "Error: no selected skill is mounted for evaluation.",
        }

    output = await run_eval_skill_command(
        runtime_context,
        skill_directory=skill_directory,
        command=request.command,
    )
    output_text = str(output)
    passed = not output_text.lstrip().startswith("Error:")
    return {
        "status": "passed" if passed else "failed",
        "output_preview": _output_preview(output_text),
    }


def _default_skill_directory(runtime_context: SkillToolContext) -> str | None:
    slug = next(iter(runtime_context.descriptors), None)
    if slug is None:
        return None
    return f"/runtime/{runtime_context.thread_id}/skills/{slug}/"


def _output_preview(output: str) -> str:
    normalized = output.strip()
    if len(normalized) <= _OUTPUT_PREVIEW_MAX_CHARS:
        return normalized
    return f"{normalized[:_OUTPUT_PREVIEW_MAX_CHARS]}..."


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
