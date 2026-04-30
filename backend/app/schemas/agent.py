from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.skill import SkillBrief as SkillBrief  # noqa: F401 — used in AgentResponse

MAX_OPENER_QUESTIONS = 12
OPENER_QUESTION_MAX_LENGTH = 200


def _validate_opener_questions(v: list[str] | None) -> list[str] | None:
    """Shared validator for opener_questions.

    - 리스트 길이 ≤ 12
    - 각 항목은 strip 후 1~200자
    """
    if v is None:
        return v
    if len(v) > MAX_OPENER_QUESTIONS:
        raise ValueError(
            f"opener_questions can have at most {MAX_OPENER_QUESTIONS} items"
        )
    cleaned: list[str] = []
    for idx, item in enumerate(v):
        if not isinstance(item, str):
            raise ValueError(f"opener_questions[{idx}] must be a string")
        stripped = item.strip()
        if not stripped:
            raise ValueError(f"opener_questions[{idx}] must not be empty")
        if len(stripped) > OPENER_QUESTION_MAX_LENGTH:
            raise ValueError(
                f"opener_questions[{idx}] must be ≤{OPENER_QUESTION_MAX_LENGTH} chars"
            )
        cleaned.append(stripped)
    return cleaned


class MiddlewareConfigEntry(BaseModel):
    """Per-agent middleware configuration."""

    type: str
    params: dict[str, Any] = {}

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        from app.agent_runtime.middleware_registry import MIDDLEWARE_REGISTRY

        if v not in MIDDLEWARE_REGISTRY:
            raise ValueError(f"Unknown middleware type: {v}")
        return v


def _validate_sub_agent_ids(v: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
    """Reject duplicates. 자기참조는 path id가 없는 schema 단계에서 차단 불가 →
    service 레이어에서 추가 검증."""
    if v is None:
        return v
    seen: set[uuid.UUID] = set()
    for sid in v:
        if sid in seen:
            raise ValueError(f"sub_agent_ids contains duplicate: {sid}")
        seen.add(sid)
    return v


class AgentCreate(BaseModel):
    # M6: extra='forbid' — 구버전 client가 tool_configs / agent_config 등
    # 이미 제거된 필드를 보내면 422로 명시적 reject. silent drop 금지.
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    system_prompt: str
    model_id: uuid.UUID
    tool_ids: list[uuid.UUID] = []
    skill_ids: list[uuid.UUID] = []
    sub_agent_ids: list[uuid.UUID] = Field(default_factory=list)
    middleware_configs: list[MiddlewareConfigEntry] = []
    template_id: uuid.UUID | None = None
    model_params: dict[str, Any] | None = None
    opener_questions: list[str] | None = None
    # Optional ordered list of fallback model ids — see model_factory.
    model_fallback_ids: list[uuid.UUID] | None = None

    @field_validator("opener_questions")
    @classmethod
    def _validate_opener_questions(cls, v: list[str] | None) -> list[str] | None:
        return _validate_opener_questions(v)

    @field_validator("sub_agent_ids")
    @classmethod
    def _validate_sub_agent_ids(cls, v: list[uuid.UUID]) -> list[uuid.UUID]:
        # Create는 None을 받지 않으므로 헬퍼 결과는 항상 list[UUID].
        cleaned = _validate_sub_agent_ids(v)
        return cleaned if cleaned is not None else []


class AgentUpdate(BaseModel):
    # M6: extra='forbid' — AgentCreate와 동일 이유.
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    model_id: uuid.UUID | None = None
    tool_ids: list[uuid.UUID] | None = None
    skill_ids: list[uuid.UUID] | None = None
    sub_agent_ids: list[uuid.UUID] | None = None
    middleware_configs: list[MiddlewareConfigEntry] | None = None
    is_favorite: bool | None = None
    model_params: dict[str, Any] | None = None
    opener_questions: list[str] | None = None
    model_fallback_ids: list[uuid.UUID] | None = None

    @field_validator("opener_questions")
    @classmethod
    def _validate_opener_questions(cls, v: list[str] | None) -> list[str] | None:
        return _validate_opener_questions(v)

    @field_validator("sub_agent_ids")
    @classmethod
    def _validate_sub_agent_ids(cls, v: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
        return _validate_sub_agent_ids(v)


class ModelBrief(BaseModel):
    id: uuid.UUID
    display_name: str

    model_config = {"from_attributes": True}


class ToolBrief(BaseModel):
    id: uuid.UUID
    name: str

    model_config = {"from_attributes": True}


class AgentBrief(BaseModel):
    """가벼운 에이전트 카드용 표현 (서브에이전트 목록 등)."""

    id: uuid.UUID
    name: str
    description: str | None = None
    image_url: str | None = None

    model_config = {"from_attributes": True}


class AgentResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    system_prompt: str
    # Optional so agents whose ``model_id`` FK target was deleted out from
    # under them (legacy data, manual cleanup, m18 wipe) still serialize.
    # The frontend renders a "no model bound" warning and prompts re-binding.
    model: ModelBrief | None = None
    tools: list[ToolBrief]
    skills: list[SkillBrief] = []
    sub_agents: list[AgentBrief] = Field(default_factory=list)
    middleware_configs: list[dict[str, Any]] = []
    status: str
    is_favorite: bool = False
    model_params: dict[str, Any] | None = None
    opener_questions: list[str] | None = None
    model_fallback_ids: list[uuid.UUID] = Field(default_factory=list)
    image_url: str | None = None
    template_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GenerateImageResponse(BaseModel):
    image_url: str
