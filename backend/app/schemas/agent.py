from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator

from app.schemas.skill import SkillBrief as SkillBrief  # noqa: F401 — used in AgentResponse


class ToolConfigEntry(BaseModel):
    """Per-agent tool configuration (e.g. webhook_url for Google Chat)."""

    tool_id: uuid.UUID
    config: dict[str, Any] = {}


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
    name: str
    description: str | None = None
    system_prompt: str
    model_id: uuid.UUID
    tool_ids: list[uuid.UUID] = []
    tool_configs: list[ToolConfigEntry] = []
    skill_ids: list[uuid.UUID] = []
    middleware_configs: list[MiddlewareConfigEntry] = []
    template_id: uuid.UUID | None = None
    model_params: dict[str, Any] | None = None


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    model_id: uuid.UUID | None = None
    tool_ids: list[uuid.UUID] | None = None
    tool_configs: list[ToolConfigEntry] | None = None
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
    agent_config: dict[str, Any] | None = None

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
