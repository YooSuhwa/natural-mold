from __future__ import annotations

from collections.abc import Sequence

from app.agent_runtime.skill_builder.eval_runner import (
    EvalCaseResult,
    validate_grader_result,
)
from app.config import settings
from app.schemas.skill_builder import JsonValue
from app.services.skill_evaluation_result_values import score as normalize_score
from app.services.skill_evaluation_worker_types import SkillEvaluationExecutionError

type JsonObject = dict[str, JsonValue]


def normalize_case_results(
    *,
    evals: Sequence[JsonValue],
    payload: JsonObject,
    execution_results: dict[int, JsonObject],
) -> list[JsonObject]:
    raw_results = payload.get("case_results")
    if not isinstance(raw_results, list):
        raise SkillEvaluationExecutionError("LLM grader result missing case_results list")
    rows_by_index = {_case_index(row): row for row in raw_results if isinstance(row, dict)}
    rows: list[JsonObject] = []
    for index, eval_case in enumerate(evals):
        raw = rows_by_index.get(index)
        if raw is None:
            raise SkillEvaluationExecutionError(f"LLM grader omitted case {index}")
        row = _case_result_row(index=index, eval_case=eval_case, raw=raw)
        execution = execution_results.get(index)
        if execution is not None:
            row["execution"] = execution
        rows.append(row)
    return rows


def summary_payload(
    *,
    evals: Sequence[JsonValue],
    case_results: list[JsonObject],
    payload: JsonObject,
    runner_version: str,
    baseline_enabled: bool = True,
) -> JsonObject:
    case_count = len(case_results)
    passed_count = sum(1 for row in case_results if row["status"] == "passed")
    failed_count = case_count - passed_count
    # A baseline-off run makes 2 calls/case (with-arm + grader, no without-arm)
    # — never report phantom without-skill runs the worker did not execute.
    without_skill_runs = case_count if baseline_enabled else 0
    model_call_count = case_count * (3 if baseline_enabled else 2)
    grader_result = validate_grader_result(
        {
            "expectations": [_case_field(case, "expected") for case in evals],
            "summary": {
                "runner_version": runner_version,
                "case_count": case_count,
                "passed_count": passed_count,
                "failed_count": failed_count,
                "pass_rate": _pass_rate(passed_count, case_count),
            },
            "execution_metrics": {
                "with_skill_runs": case_count,
                "without_skill_runs": without_skill_runs,
                "model_call_count": model_call_count,
                "tool_calls": _tool_call_count(case_results),
            },
            "timing": {
                "case_timeout_seconds": settings.skill_evaluation_case_timeout_seconds,
                "timeout_seconds": settings.skill_evaluation_run_timeout_seconds,
            },
            "claims": _claims(payload, case_results),
            "eval_feedback": _feedback(payload, case_results),
        }
    )
    summary = dict(grader_result)
    summary["runner_version"] = runner_version
    summary["case_count"] = case_count
    summary["passed_count"] = passed_count
    summary["failed_count"] = failed_count
    summary["pass_rate"] = _pass_rate(passed_count, case_count)
    return summary


def scores_from_case_results(
    case_results: list[JsonObject],
    *,
    baseline: bool,
) -> list[EvalCaseResult]:
    status_key = "baseline_status" if baseline else "status"
    score_key = "baseline_score" if baseline else "score"
    return [
        EvalCaseResult(
            case_index=int(row["case_index"]),
            passed=row[status_key] == "passed",
            score=_score(row.get(score_key)),
        )
        for row in case_results
    ]


def _case_result_row(*, index: int, eval_case: JsonValue, raw: JsonObject) -> JsonObject:
    score = _score(raw.get("score"))
    baseline_score = _score(raw.get("baseline_score"))
    return {
        "case_index": index,
        "status": _status(raw.get("status"), score),
        "score": score,
        "baseline_status": _status(raw.get("baseline_status"), baseline_score),
        "baseline_score": baseline_score,
        "input": _case_field(eval_case, "input"),
        "expected": _case_field(eval_case, "expected"),
        "grader_feedback": _text(raw.get("grader_feedback"), default="No grader feedback."),
        "evidence": _text(raw.get("evidence"), default="No evidence supplied."),
    }


def _claims(payload: JsonObject, case_results: list[JsonObject]) -> list[JsonObject]:
    raw = payload.get("claims")
    if isinstance(raw, list) and raw:
        return [item for item in raw if isinstance(item, dict)]
    return [
        {
            "case_index": row["case_index"],
            "supported": row["status"] == "passed",
            "evidence": row["evidence"],
        }
        for row in case_results
    ]


def _feedback(payload: JsonObject, case_results: list[JsonObject]) -> list[JsonObject]:
    raw = payload.get("eval_feedback")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return [
        {
            "case_index": row["case_index"],
            "severity": "info" if row["status"] == "passed" else "warning",
            "message": row["grader_feedback"],
        }
        for row in case_results
    ]


def _case_index(row: JsonObject) -> int:
    value = row.get("case_index")
    return value if isinstance(value, int) and value >= 0 else -1


def _score(value: JsonValue | None) -> float:
    score_value = normalize_score(value, default=0.0)
    return 0.0 if score_value is None else score_value


def _status(value: JsonValue | None, score: float) -> str:
    if value == "passed" or value == "failed":
        return str(value)
    return "passed" if score >= 0.5 else "failed"


def _case_field(eval_case: JsonValue, field: str) -> JsonValue:
    if isinstance(eval_case, dict):
        return eval_case.get(field)
    return None


def _text(value: JsonValue | None, *, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    return default


def _pass_rate(passed_count: int, case_count: int) -> float:
    if case_count == 0:
        return 0
    return round(passed_count / case_count, 6)


def _tool_call_count(case_results: list[JsonObject]) -> int:
    return sum(1 for row in case_results if isinstance(row.get("execution"), dict))
