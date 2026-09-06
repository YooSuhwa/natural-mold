from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_serializer

from app.models.conversation_run_input import JsonValue
from app.schemas.chat_resource_context import (
    INTERNAL_RESOURCE_CONTEXT_KEY,
    PUBLIC_RESOURCE_CONTEXT_KEY,
    FrozenChatResourceContext,
    PublicChatResourceReference,
)


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

    @computed_field
    @property
    def resource_context(self) -> list[PublicChatResourceReference]:
        raw = self.input_payload.get(INTERNAL_RESOURCE_CONTEXT_KEY)
        if raw is None:
            return []
        context = FrozenChatResourceContext.model_validate(raw)
        return [item.to_public_reference() for item in context.resources]

    @field_serializer("input_payload")
    def serialize_input_payload(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return {
            key: item
            for key, item in value.items()
            if key not in {PUBLIC_RESOURCE_CONTEXT_KEY, INTERNAL_RESOURCE_CONTEXT_KEY}
        }


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
