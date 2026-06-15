from __future__ import annotations

from collections.abc import Mapping

from app.agent_runtime.skill_builder.eval_limits import MAX_SKILL_EVAL_CASES
from app.schemas.skill_builder import JsonValue

type JsonObject = dict[str, JsonValue]


class SkillEvaluationFileAdapterError(ValueError):
    pass


def normalize_evaluation_file_payload(payload: Mapping[str, JsonValue]) -> JsonObject:
    evals = _eval_cases(payload.get("evals"))
    return {
        "schema_version": _schema_version(payload.get("schema_version")),
        "name": _name(payload),
        "description": _optional_str(payload.get("description")),
        "evals": [_normalize_case(case, index=index) for index, case in enumerate(evals)],
    }


def _eval_cases(value: JsonValue | None) -> list[JsonObject]:
    if not isinstance(value, list):
        raise SkillEvaluationFileAdapterError("eval file must include an evals list")
    if not value:
        raise SkillEvaluationFileAdapterError("eval file requires at least one case")
    if len(value) > MAX_SKILL_EVAL_CASES:
        raise SkillEvaluationFileAdapterError(f"eval file exceeds {MAX_SKILL_EVAL_CASES} cases")
    cases: list[JsonObject] = []
    for item in value:
        if not isinstance(item, dict):
            raise SkillEvaluationFileAdapterError("eval cases must be JSON objects")
        cases.append(dict(item))
    return cases


def _normalize_case(case: JsonObject, *, index: int) -> JsonObject:
    if "input" in case:
        return _moldy_case(case)
    if "prompt" in case:
        return _claude_case(case)
    raise SkillEvaluationFileAdapterError(f"eval case {index} must include input or prompt")


def _moldy_case(case: JsonObject) -> JsonObject:
    metadata = _metadata(case.get("metadata"))
    metadata.setdefault("source_schema", "moldy")
    return {
        "input": case["input"],
        "expected": case.get("expected"),
        "tags": _tags(case.get("tags")),
        "metadata": metadata,
    }


def _claude_case(case: JsonObject) -> JsonObject:
    metadata: JsonObject = {"source_schema": "claude_skill_creator"}
    external_id = case.get("id")
    if isinstance(external_id, str) and external_id:
        metadata["external_id"] = external_id
    files = _list_or_none(case.get("files"))
    if files is not None:
        metadata["files"] = files
    expectations = _list_or_none(case.get("expectations"))
    if expectations is not None:
        metadata["expectations"] = expectations
    return {
        "input": case["prompt"],
        "expected": case.get("expected_output"),
        "tags": _tags(case.get("tags")),
        "metadata": metadata,
    }


def _schema_version(value: JsonValue | None) -> int:
    if isinstance(value, bool):
        return 1
    if isinstance(value, int) and value >= 1:
        return value
    return 1


def _name(payload: Mapping[str, JsonValue]) -> str:
    name = payload.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    skill_name = payload.get("skill_name")
    if isinstance(skill_name, str) and skill_name.strip():
        return f"{skill_name.strip()} imported evals"
    return "Imported skill evaluation"


def _optional_str(value: JsonValue | None) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _metadata(value: JsonValue | None) -> JsonObject:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {}
    raise SkillEvaluationFileAdapterError("metadata must be a JSON object")


def _tags(value: JsonValue | None) -> list[JsonValue]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SkillEvaluationFileAdapterError("tags must be a list")
    return [item for item in value if isinstance(item, str)]


def _list_or_none(value: JsonValue | None) -> list[JsonValue] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise SkillEvaluationFileAdapterError("files and expectations must be lists")
    return value
