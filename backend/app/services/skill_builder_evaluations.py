from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.eval_schema import (
    SkillEvalCase,
    SkillEvalFile,
    SkillEvalSchemaError,
    parse_evals_json,
)
from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.schemas.skill_builder import JsonValue, SkillDraftPackage
from app.services import skill_evaluation_service
from app.services.skill_builder_errors import SkillBuilderValidationError

type JsonObject = dict[str, JsonValue]

DEFAULT_RUNNER_VERSION: Final = "builder-1"
DEFAULT_GRADER_PROMPT_VERSION: Final = "builder-grader-1"


@dataclass(frozen=True, slots=True)
class BuilderEvaluationPayload:
    name: str
    evals: list[JsonValue]
    eval_schema_version: int
    template_key: str | None
    template_version: str | None
    generation_strategy: JsonObject | None
    result: JsonObject | None


async def persist_builder_evaluation_records(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    skill: Skill,
    payload: BuilderEvaluationPayload | None,
) -> SkillEvaluationSet | None:
    if payload is None:
        return None
    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db,
        user_id=user_id,
        skill=skill,
        name=payload.name,
        evals=payload.evals,
        description="Generated during Skill Builder confirmation.",
        source_kind="builder",
        template_key=payload.template_key,
        template_version=payload.template_version,
        generation_strategy=payload.generation_strategy,
    )
    if payload.result is not None:
        db.add(
            _completed_run(
                user_id=user_id,
                skill=skill,
                evaluation_set=evaluation_set,
                payload=payload,
            )
        )
        await db.flush()
    return evaluation_set


def extract_builder_evaluation_payload(
    draft: SkillDraftPackage,
    eval_result: JsonObject | None,
) -> BuilderEvaluationPayload | None:
    parsed = _parse_draft_evals(draft)
    if parsed is None and eval_result is not None:
        parsed = _parse_result_evals(eval_result)
    if parsed is None:
        return None

    metadata = draft.evals or eval_result or {}
    return BuilderEvaluationPayload(
        name=parsed.name or f"{draft.name} builder evals",
        evals=[_case_to_json(case) for case in parsed.evals],
        eval_schema_version=parsed.schema_version,
        template_key=_string_field(metadata, "template_key"),
        template_version=_string_field(metadata, "template_version"),
        generation_strategy=_json_object_field(metadata, "generation_strategy"),
        result=eval_result,
    )


def _completed_run(
    *,
    user_id: uuid.UUID,
    skill: Skill,
    evaluation_set: SkillEvaluationSet,
    payload: BuilderEvaluationPayload,
) -> SkillEvaluationRun:
    result = payload.result or {}
    now = _now()
    return SkillEvaluationRun(
        user_id=user_id,
        skill_id=skill.id,
        evaluation_set_id=evaluation_set.id,
        status="completed",
        skill_version=skill.version,
        skill_content_hash=skill.content_hash,
        runner_model=_string_field(result, "runner_model"),
        runner_version=_string_field(result, "runner_version") or DEFAULT_RUNNER_VERSION,
        grader_prompt_version=_string_field(result, "grader_prompt_version")
        or DEFAULT_GRADER_PROMPT_VERSION,
        eval_schema_version=_int_field(result, "eval_schema_version")
        or payload.eval_schema_version,
        run_config=_json_object_field(result, "run_config"),
        estimate=_json_object_field(result, "estimate"),
        summary=_json_object_field(result, "summary"),
        benchmark=_json_object_field(result, "benchmark"),
        case_results=_json_list_field(result, "case_results"),
        artifact_path=_string_field(result, "artifact_path"),
        started_at=now,
        completed_at=now,
    )


def _parse_draft_evals(draft: SkillDraftPackage) -> SkillEvalFile | None:
    for file in draft.files:
        if file.path == "evals/evals.json":
            return _parse_evals_content(file.content, path=file.path)
    if draft.evals is None:
        return None
    return _parse_evals_value(draft.evals, path="draft.evals")


def _parse_result_evals(eval_result: JsonObject) -> SkillEvalFile | None:
    evals = eval_result.get("evals")
    if isinstance(evals, dict):
        return _parse_evals_value(evals, path="session.eval_result.evals")
    if isinstance(evals, list):
        return _parse_evals_value({"evals": evals}, path="session.eval_result.evals")
    cases = eval_result.get("cases")
    if isinstance(cases, list):
        return _parse_evals_value({"evals": cases}, path="session.eval_result.cases")
    return None


def _parse_evals_value(value: JsonValue, *, path: str) -> SkillEvalFile:
    return _parse_evals_content(json.dumps(value, ensure_ascii=False), path=path)


def _parse_evals_content(content: str, *, path: str) -> SkillEvalFile:
    try:
        return parse_evals_json(content)
    except SkillEvalSchemaError as exc:
        raise SkillBuilderValidationError(_eval_error_result(path, str(exc))) from exc


def _case_to_json(case: SkillEvalCase) -> JsonObject:
    return {
        "input": case.input,
        "expected": case.expected,
        "tags": list(case.tags),
        "metadata": dict(case.metadata),
    }


def _string_field(source: JsonObject, key: str) -> str | None:
    value = source.get(key)
    if isinstance(value, str) and value:
        return value
    return None


def _int_field(source: JsonObject, key: str) -> int | None:
    value = source.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _json_object_field(source: JsonObject, key: str) -> JsonObject | None:
    value = source.get(key)
    if isinstance(value, dict):
        return value
    return None


def _json_list_field(source: JsonObject, key: str) -> list[JsonValue] | None:
    value = source.get(key)
    if isinstance(value, list):
        return list(value)
    return None


def _eval_error_result(path: str, message: str) -> JsonObject:
    return {
        "valid": False,
        "error_count": 1,
        "warning_count": 0,
        "info_count": 0,
        "issues": [
            {
                "code": "EVALS_JSON_INVALID",
                "severity": "error",
                "path": path,
                "message": message,
            }
        ],
    }


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
