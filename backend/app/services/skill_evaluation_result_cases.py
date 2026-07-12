from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from app.schemas.skill_builder import JsonValue
from app.services.skill_evaluation_result_values import (
    JsonObject,
    bool_or_none,
    first_present,
    json_object,
    number_or_none,
    review_status,
    score,
    status,
    string_or_none,
)

LEGACY_CASE_RESULT_FIELDS: Final = ("execution", "notes")


def normalize_case_results(
    *,
    evals: Sequence[JsonValue],
    raw_case_results: Sequence[JsonValue],
) -> list[JsonObject]:
    rows_by_index: dict[int, JsonObject] = {}
    for fallback_index, raw in enumerate(raw_case_results):
        row = json_object(raw)
        if row is None:
            continue
        rows_by_index[_case_index(row, fallback_index=fallback_index)] = row
    return [
        _case_result_row(index=index, eval_case=eval_case, raw=rows_by_index.get(index, {}))
        for index, eval_case in enumerate(evals)
    ]


def status_count(case_results: list[JsonObject], key: str, expected_status: str) -> int:
    return sum(1 for row in case_results if row.get(key) == expected_status)


def baseline_count(case_results: list[JsonObject]) -> int:
    return sum(1 for row in case_results if row["baseline_status"] is not None)


def mean_metric(case_results: list[JsonObject], key: str) -> int | float | None:
    values = [number_or_none(row.get(key)) for row in case_results]
    numbers = [value for value in values if value is not None]
    if not numbers:
        return None
    mean = sum(numbers) / len(numbers)
    return int(mean) if mean.is_integer() else round(mean, 6)


def _case_result_row(*, index: int, eval_case: JsonValue, raw: JsonObject) -> JsonObject:
    score_value = score(raw.get("score"), default=0.0)
    baseline_score = score(raw.get("baseline_score"), default=None)
    row: JsonObject = {
        "case_index": index,
        "name": first_present(raw.get("name"), _case_field(eval_case, "name")),
        "input": first_present(raw.get("input"), _case_field(eval_case, "input")),
        "expected": first_present(raw.get("expected"), _case_field(eval_case, "expected")),
        "status": status(raw.get("status"), score_value=score_value),
        "score": score_value,
        "baseline_status": status(raw.get("baseline_status"), score_value=baseline_score),
        "baseline_score": baseline_score,
        "triggered": bool_or_none(raw.get("triggered")),
        "baseline_triggered": bool_or_none(raw.get("baseline_triggered")),
        "duration_ms": number_or_none(raw.get("duration_ms")),
        "baseline_duration_ms": number_or_none(raw.get("baseline_duration_ms")),
        "tokens": number_or_none(raw.get("tokens")),
        "baseline_tokens": number_or_none(raw.get("baseline_tokens")),
        "error": string_or_none(raw.get("error")),
        "evidence": string_or_none(raw.get("evidence")),
        "grader_feedback": string_or_none(raw.get("grader_feedback")),
        "review_status": review_status(raw.get("review_status")),
        # llm-2 measured arms (Phase 3 §4) — None for legacy runners.
        "with_answer_preview": string_or_none(raw.get("with_answer_preview")),
        "without_answer_preview": string_or_none(raw.get("without_answer_preview")),
    }
    for field in LEGACY_CASE_RESULT_FIELDS:
        value = raw.get(field)
        if value is not None:
            row[field] = value
    return row


def _case_index(row: JsonObject, *, fallback_index: int) -> int:
    value = row.get("case_index")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return fallback_index


def _case_field(eval_case: JsonValue, field: str) -> JsonValue:
    if isinstance(eval_case, dict):
        return eval_case.get(field)
    return None
