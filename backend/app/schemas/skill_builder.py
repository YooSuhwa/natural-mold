from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class SkillBuilderMode(StrEnum):
    CREATE = "create"
    IMPROVE = "improve"


class SkillBuilderStatus(StrEnum):
    # v2 상태 기계: active → confirming → completed (+abandoned = GC 대상).
    ACTIVE = "active"
    ABANDONED = "abandoned"
    # 구 one-pass 플로우 레거시 값 — 기존 row 호환용.
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


class SkillDraftFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str = Field(..., min_length=1, max_length=500)
    content: str
    media_type: str = "text/plain"
    role: Literal["skill", "script", "reference", "asset", "metadata", "eval"] = "skill"


class SkillBuilderFileEntry(BaseModel):
    """드래프트 워크스페이스 파일 요약 (레일 소스 뷰, M7) — 내용 없음."""

    model_config = ConfigDict(frozen=True)

    path: str
    size: int
    role: str


class SkillBuilderFilesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    files: list[SkillBuilderFileEntry]


class SkillBuilderFileContentResponse(BaseModel):
    """드래프트 파일 내용 (소유자 전용 조회 — 레일 소스 뷰어)."""

    model_config = ConfigDict(frozen=True)

    path: str
    role: str
    content: str


class SkillDraftPackage(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1, max_length=160)
    slug: str = Field(..., min_length=1, max_length=160)
    description: str = Field(..., min_length=1, max_length=1000)
    files: list[SkillDraftFile] = Field(default_factory=list)
    credential_requirements: list[dict[str, JsonValue]] = Field(default_factory=list)
    execution_profile: dict[str, JsonValue] = Field(default_factory=dict)
    validation_issues: list[dict[str, JsonValue]] = Field(default_factory=list)
    compatibility_result: dict[str, JsonValue] | None = None
    changelog_draft: dict[str, JsonValue] | None = None
    evals: dict[str, JsonValue] | None = None
    benchmark: dict[str, JsonValue] | None = None


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
    # v2 (빌더 챗): 빌더 대화/히든 에이전트 식별자. conversation_id는 세션
    # 컬럼에서, agent_id는 대화 역참조로 라우터가 채운다 (ORM 속성 아님).
    conversation_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
