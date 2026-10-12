from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.artifact import ArtifactSummary
from app.schemas.conversation_refs import ConversationHistoryRef
from app.schemas.conversation_run import ConversationRunHistoryResponse
from app.schemas.conversation_run_input import ConversationRunInputResponse

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class ConversationHistoryManifestResponse(BaseModel):
    """Immutable retained-history manifest exposed to lifecycle callers."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    conversation_id: UUID
    schema_version: Literal[1]
    source_agent_id_snapshot: UUID
    state: Literal["building", "complete"]
    checkpoint_count: int
    event_count: int
    canonical_payload_sha256: str
    created_at: datetime
    updated_at: datetime


class ConversationHistoryEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    assistant_message_id: str
    sequence_start: int
    sequence_end: int
    events: list[JsonValue]
    payload_sha256: str
    created_at: datetime


class ConversationHistoryCheckpointResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    checkpoint_id: str
    parent_checkpoint_id: str | None
    checkpoint_namespace: str
    channel_values: dict[str, JsonValue]
    messages: list[dict[str, JsonValue]]
    branch: dict[str, JsonValue]
    trace: dict[str, JsonValue]
    payload_sha256: str


class ConversationHistoryUsageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    run_id: UUID | None
    agent_id: UUID | None
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost: float | None
    created_at: datetime


class ConversationHistoryAttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    message_id: str | None
    filename: str
    mime_type: str
    size_bytes: int
    url: str
    created_at: datetime


class ConversationHistoryLinks(BaseModel):
    model_config = ConfigDict(frozen=True)

    self: str
    messages: str
    state: str
    trace: str
    files: str


class ConversationHistoryResponse(BaseModel):
    """Stable owner-only history envelope for every Agent lifecycle state."""

    model_config = ConfigDict(frozen=True)

    conversation: ConversationHistoryRef
    manifest: ConversationHistoryManifestResponse | None = None
    messages: list[JsonValue] = Field(default_factory=list)
    branches: list[JsonValue] = Field(default_factory=list)
    state: dict[str, JsonValue] = Field(default_factory=dict)
    trace: dict[str, JsonValue] = Field(default_factory=dict)
    checkpoints: list[JsonValue] = Field(default_factory=list)
    events: list[ConversationHistoryEventResponse] = Field(default_factory=list)
    latest_run: ConversationRunHistoryResponse | None = None
    runs: list[ConversationRunHistoryResponse] = Field(default_factory=list)
    inputs: list[ConversationRunInputResponse] = Field(default_factory=list)
    usage: list[ConversationHistoryUsageResponse] = Field(default_factory=list)
    attachments: list[ConversationHistoryAttachmentResponse] = Field(default_factory=list)
    artifacts: list[ArtifactSummary] = Field(default_factory=list)
    links: ConversationHistoryLinks


__all__ = [
    "ConversationHistoryAttachmentResponse",
    "ConversationHistoryCheckpointResponse",
    "ConversationHistoryEventResponse",
    "ConversationHistoryLinks",
    "ConversationHistoryManifestResponse",
    "ConversationHistoryResponse",
    "ConversationHistoryUsageResponse",
    "JsonValue",
]
