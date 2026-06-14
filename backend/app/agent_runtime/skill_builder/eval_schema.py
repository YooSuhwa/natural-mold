from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.schemas.skill_builder import JsonValue


class SkillEvalSchemaError(ValueError):
    pass


class SkillEvalCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    input: JsonValue
    expected: JsonValue | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class SkillEvalFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = Field(default=1, ge=1)
    name: str | None = Field(default=None, max_length=160)
    evals: list[SkillEvalCase] = Field(..., min_length=1)


def parse_evals_json(content: str) -> SkillEvalFile:
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        raise SkillEvalSchemaError(f"invalid JSON in evals/evals.json: {exc.msg}") from exc
    if isinstance(raw, list):
        raw = {"evals": raw}
    try:
        return SkillEvalFile.model_validate(raw)
    except ValidationError as exc:
        raise SkillEvalSchemaError("invalid evals/evals.json schema") from exc
