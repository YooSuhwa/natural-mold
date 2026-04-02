from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ToolConfigEntry(BaseModel):
    """Per-agent tool configuration (e.g. webhook_url for Google Chat)."""

    tool_id: uuid.UUID
    config: dict[str, Any] = {}


class AgentCreate(BaseModel):
    name: str
    description: str | None = None
    system_prompt: str
    model_id: uuid.UUID
    tool_ids: list[uuid.UUID] = []
    tool_configs: list[ToolConfigEntry] = []
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


class SkillBrief(BaseModel):
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
    status: str
    is_favorite: bool = False
    model_params: dict[str, Any] | None = None
    template_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
