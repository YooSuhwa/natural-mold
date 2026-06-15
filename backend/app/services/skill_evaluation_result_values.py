from __future__ import annotations

from typing import Final

from app.schemas.skill_builder import JsonValue

type JsonObject = dict[str, JsonValue]

STATUS_PASSED: Final = "passed"
STATUS_FAILED: Final = "failed"
STATUS_ERROR: Final = "error"
REVIEW_UNREVIEWED: Final = "unreviewed"
HIGHER_IS_BETTER: Final = "higher_is_better"
LOWER_IS_BETTER: Final = "lower_is_better"


def number_or_none(value: JsonValue | None) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    return None


def number_or_default(value: JsonValue | None, default: int | float) -> int | float:
    number = number_or_none(value)
    return default if number is None else number


def string_or_none(value: JsonValue | None) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def bool_or_none(value: JsonValue | None) -> bool | None:
    return value if isinstance(value, bool) else None


def json_object(value: JsonValue) -> JsonObject | None:
    if isinstance(value, dict):
        return dict(value)
    return None


def list_value(value: JsonValue | None) -> list[JsonValue]:
    if isinstance(value, list):
        return value
    return []


def dict_value(value: JsonValue | None) -> JsonObject:
    if isinstance(value, dict):
        return dict(value)
    return {}


def first_present(primary: JsonValue, fallback: JsonValue) -> JsonValue:
    return fallback if primary is None else primary


def rate(passed: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(passed / total, 6)


def delta(
    value: int | float | JsonValue | None,
    baseline: int | float | JsonValue | None,
) -> int | float | None:
    left = number_or_none(value)
    right = number_or_none(baseline)
    if left is None or right is None:
        return None
    difference = left - right
    return int(difference) if float(difference).is_integer() else round(difference, 6)


def delta_rate(
    difference: int | float | None,
    baseline: int | float | None,
) -> float | None:
    if difference is None or baseline in (None, 0):
        return None
    return round(difference / baseline, 6)


def score(value: JsonValue | None, *, default: float | None) -> float | None:
    number = number_or_none(value)
    if number is None:
        return default
    return max(0.0, min(1.0, float(number)))


def status(value: JsonValue | None, *, score_value: float | None) -> str | None:
    if value is None:
        if score_value is None:
            return None
        return STATUS_PASSED if score_value >= 0.5 else STATUS_FAILED
    if not isinstance(value, str):
        return STATUS_ERROR
    match value:
        case "passed" | "failed" | "error" | "skipped":
            return value
        case _:
            return STATUS_ERROR


def review_status(value: JsonValue | None) -> str:
    if not isinstance(value, str):
        return REVIEW_UNREVIEWED
    match value:
        case "unreviewed" | "accepted" | "rejected" | "needs_rerun":
            return value
        case _:
            return REVIEW_UNREVIEWED
