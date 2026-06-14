from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.skill_builder import JsonValue


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
    evals: list[JsonValue] = Field(..., min_length=1)


class SkillEvaluationSetUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    evals: list[JsonValue] | None = Field(default=None, min_length=1)


class SkillEvaluationRunEstimate(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_count: int = Field(..., ge=0)
    model_call_count: int = Field(..., ge=0)
    estimated_seconds: int = Field(..., ge=0)
    timeout_seconds: int = Field(..., ge=1)
    estimated_cost_usd: float = Field(..., ge=0)
    uses_baseline_comparison: bool


class SkillEvaluationRunCancelRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    reason: str = Field(default="user", max_length=120)


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
