from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class SkillBuilderMode(StrEnum):
    CREATE = "create"
    IMPROVE = "improve"


class SkillBuilderStatus(StrEnum):
    COLLECTING = "collecting"
    DRAFTING = "drafting"
    REVIEW = "review"
    CONFIRMING = "confirming"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SkillBuilderStartRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: SkillBuilderMode = SkillBuilderMode.CREATE
    user_request: str = Field(..., min_length=1, max_length=4000)
    source_skill_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def require_source_skill_for_improve(self) -> Self:
        if self.mode is SkillBuilderMode.IMPROVE and self.source_skill_id is None:
            raise PydanticCustomError(
                "source_skill_required",
                "source_skill_id is required in improve mode",
            )
        return self


class SkillBuilderMessageRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str = Field(..., min_length=1, max_length=8000)


class SkillBuilderSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    user_id: uuid.UUID
    user_request: str
    mode: SkillBuilderMode
    status: SkillBuilderStatus
    current_phase: int
    source_skill_id: uuid.UUID | None = None
    base_skill_version: str | None = None
    base_content_hash: str | None = None
    base_snapshot: dict[str, JsonValue] | None = None
    messages: list[JsonValue] | None = None
    intent: dict[str, JsonValue] | None = None
    draft_package: dict[str, JsonValue] | None = None
    validation_result: dict[str, JsonValue] | None = None
    compatibility_result: dict[str, JsonValue] | None = None
    changelog_draft: dict[str, JsonValue] | None = None
    eval_result: dict[str, JsonValue] | None = None
    trigger_eval_result: dict[str, JsonValue] | None = None
    finalized_skill_id: uuid.UUID | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
