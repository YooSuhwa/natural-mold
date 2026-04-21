from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas.skill import SkillBrief as SkillBrief  # noqa: F401 — used in AgentResponse


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
    middleware_configs: list[MiddlewareConfigEntry] = []
    template_id: uuid.UUID | None = None
    model_params: dict[str, Any] | None = None


class AgentUpdate(BaseModel):
    # M6: extra='forbid' — AgentCreate와 동일 이유.
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    model_id: uuid.UUID | None = None
    tool_ids: list[uuid.UUID] | None = None
    skill_ids: list[uuid.UUID] | None = None
    middleware_configs: list[MiddlewareConfigEntry] | None = None
    is_favorite: bool | None = None
    model_params: dict[str, Any] | None = None


class ModelBrief(BaseModel):
    id: uuid.UUID
    display_name: str

    model_config = {"from_attributes": True}


class ToolBrief(BaseModel):
    id: uuid.UUID
    name: str

    model_config = {"from_attributes": True}


class AgentResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    system_prompt: str
    model: ModelBrief
    tools: list[ToolBrief]
    skills: list[SkillBrief] = []
    middleware_configs: list[dict[str, Any]] = []
    status: str
    is_favorite: bool = False
    model_params: dict[str, Any] | None = None
    image_url: str | None = None
    template_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GenerateImageResponse(BaseModel):
    image_url: str
