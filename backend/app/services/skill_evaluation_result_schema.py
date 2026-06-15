from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.schemas.skill_builder import JsonValue
from app.services.skill_evaluation_result_cases import (
    baseline_count,
    mean_metric,
    normalize_case_results,
    status_count,
)
from app.services.skill_evaluation_result_values import (
    HIGHER_IS_BETTER,
    LOWER_IS_BETTER,
    STATUS_PASSED,
    JsonObject,
    delta,
    delta_rate,
    dict_value,
    first_present,
    list_value,
    number_or_default,
    number_or_none,
    rate,
)


def normalize_skill_evaluation_result(
    *,
    evals: Sequence[JsonValue],
    raw_case_results: Sequence[JsonValue],
    raw_summary: Mapping[str, JsonValue] | None = None,
    raw_benchmark: Mapping[str, JsonValue] | None = None,
) -> tuple[JsonObject, JsonObject, list[JsonObject]]:
    case_results = normalize_case_results(evals=evals, raw_case_results=raw_case_results)
    benchmark = _benchmark(raw_benchmark=raw_benchmark, case_results=case_results)
    summary = _summary(
        raw_summary=raw_summary,
        benchmark=benchmark,
        case_results=case_results,
    )
    return summary, benchmark, case_results


def _summary(
    *,
    raw_summary: Mapping[str, JsonValue] | None,
    benchmark: JsonObject,
    case_results: list[JsonObject],
) -> JsonObject:
    summary: JsonObject = dict(raw_summary or {})
    case_count = len(case_results)
    passed_count = sum(1 for row in case_results if row["status"] == STATUS_PASSED)
    failed_count = case_count - passed_count
    duration = mean_metric(case_results, "duration_ms")
    baseline_duration = mean_metric(case_results, "baseline_duration_ms")
    tokens = mean_metric(case_results, "tokens")
    baseline_tokens = mean_metric(case_results, "baseline_tokens")
    token_delta = delta(tokens, baseline_tokens)
    summary["schema_version"] = 2
    summary["case_count"] = case_count
    summary["passed_count"] = passed_count
    summary["failed_count"] = failed_count
    summary["pass_rate"] = rate(passed_count, case_count)
    summary["trigger_accuracy"] = _trigger_accuracy(case_results)
    summary["average_duration_ms"] = duration
    summary["average_tokens"] = tokens
    summary["token_delta"] = token_delta
    summary["claims"] = list_value(summary.get("claims"))
    summary["eval_feedback"] = list_value(summary.get("eval_feedback"))
    summary["execution_metrics"] = dict_value(summary.get("execution_metrics"))
    summary["timing"] = dict_value(summary.get("timing"))
    summary["kpis"] = _kpis(
        benchmark=benchmark,
        case_results=case_results,
        duration=duration,
        baseline_duration=baseline_duration,
        tokens=tokens,
        baseline_tokens=baseline_tokens,
    )
    return summary


def _benchmark(
    *,
    raw_benchmark: Mapping[str, JsonValue] | None,
    case_results: list[JsonObject],
) -> JsonObject:
    benchmark: JsonObject = dict(raw_benchmark or {})
    with_pass_rate = rate(status_count(case_results, "status", STATUS_PASSED), len(case_results))
    baseline_total = baseline_count(case_results)
    baseline_pass_rate = (
        rate(status_count(case_results, "baseline_status", STATUS_PASSED), baseline_total)
        if baseline_total
        else None
    )
    benchmark.setdefault("case_count", len(case_results))
    benchmark["with_skill_pass_rate"] = number_or_default(
        benchmark.get("with_skill_pass_rate"), with_pass_rate
    )
    benchmark["without_skill_pass_rate"] = first_present(
        number_or_none(benchmark.get("without_skill_pass_rate")), baseline_pass_rate
    )
    benchmark["pass_rate_delta"] = first_present(
        number_or_none(benchmark.get("pass_rate_delta")),
        delta(benchmark["with_skill_pass_rate"], benchmark["without_skill_pass_rate"]),
    )
    duration = mean_metric(case_results, "duration_ms")
    baseline_duration = mean_metric(case_results, "baseline_duration_ms")
    tokens = mean_metric(case_results, "tokens")
    baseline_tokens = mean_metric(case_results, "baseline_tokens")
    benchmark["duration_delta_ms"] = first_present(
        number_or_none(benchmark.get("duration_delta_ms")),
        delta(duration, baseline_duration),
    )
    benchmark["token_delta"] = first_present(
        number_or_none(benchmark.get("token_delta")),
        delta(tokens, baseline_tokens),
    )
    benchmark["quality_delta"] = first_present(
        number_or_none(benchmark.get("quality_delta")),
        number_or_none(benchmark.get("pass_rate_delta")),
    )
    benchmark["comparison"] = _comparison(benchmark=benchmark, case_results=case_results)
    return benchmark


def _kpis(
    *,
    benchmark: JsonObject,
    case_results: list[JsonObject],
    duration: int | float | None,
    baseline_duration: int | float | None,
    tokens: int | float | None,
    baseline_tokens: int | float | None,
) -> JsonObject:
    case_count = len(case_results)
    passed_count = status_count(case_results, "status", STATUS_PASSED)
    baseline_total = baseline_count(case_results)
    error_count = case_count - passed_count
    baseline_errors = (
        baseline_total - status_count(case_results, "baseline_status", STATUS_PASSED)
        if baseline_total
        else None
    )
    token_delta = delta(tokens, baseline_tokens)
    return {
        "pass_rate": {
            "value": rate(passed_count, case_count),
            "passed": passed_count,
            "total": case_count,
            "delta": number_or_none(benchmark.get("pass_rate_delta")),
            "direction": HIGHER_IS_BETTER,
        },
        "trigger_accuracy": _trigger_kpi(case_results),
        "average_duration_ms": {
            "value": duration,
            "baseline_value": baseline_duration,
            "delta": delta(duration, baseline_duration),
            "direction": LOWER_IS_BETTER,
        },
        "average_tokens": {
            "value": tokens,
            "baseline_value": baseline_tokens,
            "delta": token_delta,
            "delta_rate": delta_rate(token_delta, baseline_tokens),
            "direction": LOWER_IS_BETTER,
        },
        "error_count": {
            "value": error_count,
            "baseline_value": baseline_errors,
            "delta": delta(error_count, baseline_errors),
            "direction": LOWER_IS_BETTER,
        },
    }


def _comparison(*, benchmark: JsonObject, case_results: list[JsonObject]) -> JsonObject:
    duration = mean_metric(case_results, "duration_ms")
    baseline_duration = mean_metric(case_results, "baseline_duration_ms")
    tokens = mean_metric(case_results, "tokens")
    baseline_tokens = mean_metric(case_results, "baseline_tokens")
    return {
        "pass_rate": {
            "with_skill": number_or_none(benchmark.get("with_skill_pass_rate")),
            "baseline": number_or_none(benchmark.get("without_skill_pass_rate")),
            "delta": number_or_none(benchmark.get("pass_rate_delta")),
            "direction": HIGHER_IS_BETTER,
        },
        "duration_ms": {
            "with_skill": duration,
            "baseline": baseline_duration,
            "delta": delta(duration, baseline_duration),
            "direction": LOWER_IS_BETTER,
        },
        "tokens": {
            "with_skill": tokens,
            "baseline": baseline_tokens,
            "delta": delta(tokens, baseline_tokens),
            "direction": LOWER_IS_BETTER,
        },
    }


def _trigger_kpi(case_results: list[JsonObject]) -> JsonObject:
    triggered = [row["triggered"] for row in case_results if row["triggered"] is not None]
    passed = sum(1 for value in triggered if value is True)
    total = len(case_results)
    return {
        "value": rate(passed, total) if triggered else None,
        "passed": passed if triggered else None,
        "total": total,
        "delta": None,
        "direction": HIGHER_IS_BETTER,
    }


def _trigger_accuracy(case_results: list[JsonObject]) -> float | None:
    triggered = [row["triggered"] for row in case_results if row["triggered"] is not None]
    if not triggered:
        return None
    return rate(sum(1 for value in triggered if value is True), len(triggered))
