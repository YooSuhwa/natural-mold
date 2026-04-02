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


class SuggestedReplies(BaseModel):
    options: list[str] = []
    multi_select: bool = False


class RecommendedTool(BaseModel):
    name: str
    description: str


class CreationMessageResponse(BaseModel):
    role: str
    content: str
    current_phase: int = 1
    phase_result: str | None = None
    question: str | None = None
    draft_config: DraftConfig | None = None
    suggested_replies: SuggestedReplies | None = None
    recommended_tools: list[RecommendedTool] = []
