from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.conversation_run_input import JsonValue


class ConversationRunInputResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    run_id: uuid.UUID | None
    client_request_id: str
    source: str
    status: str
    priority: int
    position: int
    revision: int
    input_payload: dict[str, JsonValue]
    attachment_ids: list[str]
    checkpoint_id: str | None
    claimed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ConversationRunInputListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    queue_paused: bool
    items: list[ConversationRunInputResponse]


class ConversationRunInputEditRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    expected_revision: int = Field(ge=1)
    input: dict[str, JsonValue]


class ConversationRunInputReorderRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    ordered_input_ids: list[uuid.UUID]
    expected_revisions: dict[uuid.UUID, int]


class ConversationRunInputPromoteRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    expected_revision: int = Field(ge=1)
