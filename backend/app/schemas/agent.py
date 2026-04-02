from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class AgentCreate(BaseModel):
    name: str
    description: str | None = None
    system_prompt: str
    model_id: uuid.UUID
    tool_ids: list[uuid.UUID] = []
    template_id: uuid.UUID | None = None


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    model_id: uuid.UUID | None = None
    tool_ids: list[uuid.UUID] | None = None


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
    status: str
    template_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
