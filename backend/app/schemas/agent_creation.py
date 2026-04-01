from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class CreationMessageRequest(BaseModel):
    content: str


class DraftConfig(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    recommended_tool_names: list[str] = []
    recommended_model: str | None = None
    is_ready: bool = False


class CreationSessionResponse(BaseModel):
    id: uuid.UUID
    status: str
    conversation_history: list[dict[str, Any]]
    draft_config: DraftConfig | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CreationMessageResponse(BaseModel):
    role: str
    content: str
    draft_config: DraftConfig | None = None
