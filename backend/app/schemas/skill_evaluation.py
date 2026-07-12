from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from app.agent_runtime.skill_builder.eval_limits import MAX_SKILL_EVAL_CASES
from app.schemas.skill_builder import JsonValue
from app.services.skill_evaluation_case_limits import (
    SkillEvaluationCaseSizeError,
    validate_evaluation_case_sizes,
)


class SkillEvaluationRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    GRADING = "grading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SkillEvaluationSetCreate(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1, max_length=160)
    description: str | None = None
    evals: list[JsonValue] = Field(..., min_length=1, max_length=MAX_SKILL_EVAL_CASES)

    @field_validator("evals")
    @classmethod
    def validate_eval_case_sizes(cls, value: list[JsonValue]) -> list[JsonValue]:
        return _bounded_eval_cases(value)


class SkillEvaluationSetUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    evals: list[JsonValue] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_SKILL_EVAL_CASES,
    )

    @field_validator("evals")
    @classmethod
    def validate_eval_case_sizes(cls, value: list[JsonValue] | None) -> list[JsonValue] | None:
        if value is None:
            return None
        return _bounded_eval_cases(value)


def _bounded_eval_cases(value: list[JsonValue]) -> list[JsonValue]:
    try:
        validate_evaluation_case_sizes(value)
    except SkillEvaluationCaseSizeError as exc:
        raise PydanticCustomError("skill_eval_case_too_large", str(exc)) from exc
    return value


class SkillEvaluationRunEstimate(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_count: int = Field(..., ge=0)
    model_call_count: int = Field(..., ge=0)
    estimated_seconds: int = Field(..., ge=0)
    timeout_seconds: int = Field(..., ge=1)
    estimated_tokens_in: int = Field(default=0, ge=0)
    estimated_tokens_out: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(..., ge=0)
    # False → the runner model has no per-token pricing; estimated_cost_usd=0
    # then means "unknown", not "free" (spec §5.2).
    pricing_available: bool = False
    runner_model: str | None = None
    uses_baseline_comparison: bool


class SkillEvaluationRunCreateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    # False skips the measured without-arm (2 calls/case instead of 3).
    baseline_comparison: bool = True


class SkillEvaluationRunCancelRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    reason: str = Field(default="user", max_length=120)


class SkillEvaluationPrepareRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    allow_llm_generation: bool = True
    force: bool = False


class SkillEvaluationPrepareResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    evaluation_set_id: uuid.UUID | None = None
    source_kind: str
    case_count: int = Field(..., ge=0)
    payload_hash: str | None = None
    reason: str | None = None


class SkillEvaluationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    skill_id: uuid.UUID
    evaluation_set_id: uuid.UUID
    status: SkillEvaluationRunStatus
    skill_version: str | None = None
    skill_content_hash: str | None = None
    runner_model: str | None = None
    summary: dict[str, JsonValue] | None = None
    benchmark: dict[str, JsonValue] | None = None
    # Measured LLM usage rollup — {model_calls, tokens_in, tokens_out,
    # cost_usd, measured}. None for legacy/deterministic runs.
    usage: dict[str, JsonValue] | None = None
    case_results: list[JsonValue] | None = None
    error_message: str | None = None
    cancellation_requested_at: datetime | None = None
    cancellation_reason: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SkillEvaluationSetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    skill_id: uuid.UUID
    name: str
    description: str | None = None
    source_kind: str
    evals: list[JsonValue]
    expectations_schema_version: int
    latest_run: SkillEvaluationRunResponse | None = None
    created_at: datetime
    updated_at: datetime
