from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from app.agent_runtime.skill_builder.eval_limits import (
    MAX_SKILL_EVAL_CASE_BYTES,
    MAX_SKILL_EVAL_FIELD_BYTES,
)
from app.schemas.skill_builder import JsonValue


@dataclass(frozen=True, slots=True)
class SkillEvaluationCaseSizeError(ValueError):
    case_index: int
    field_name: str
    size_bytes: int
    max_bytes: int

    def __str__(self) -> str:
        return (
            f"case {self.case_index} {self.field_name} is {self.size_bytes} bytes; "
            f"maximum is {self.max_bytes}"
        )


def validate_evaluation_case_sizes(evals: Sequence[JsonValue]) -> None:
    for index, case in enumerate(evals):
        match case:
            case dict() as case_mapping:
                _validate_case_fields(index, case_mapping)
            case _:
                pass
        _raise_if_too_large(
            case_index=index,
            field_name="case",
            size_bytes=_json_size(case),
            max_bytes=MAX_SKILL_EVAL_CASE_BYTES,
        )


def _validate_case_fields(case_index: int, case: dict[str, JsonValue]) -> None:
    for field_name in ("input", "expected"):
        if field_name not in case:
            continue
        _raise_if_too_large(
            case_index=case_index,
            field_name=field_name,
            size_bytes=_json_size(case[field_name]),
            max_bytes=MAX_SKILL_EVAL_FIELD_BYTES,
        )


def _raise_if_too_large(
    *,
    case_index: int,
    field_name: str,
    size_bytes: int,
    max_bytes: int,
) -> None:
    if size_bytes > max_bytes:
        raise SkillEvaluationCaseSizeError(
            case_index=case_index,
            field_name=field_name,
            size_bytes=size_bytes,
            max_bytes=max_bytes,
        )


def _json_size(value: JsonValue) -> int:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return len(encoded)
