from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.schemas.conversation_history import JsonValue


@dataclass(frozen=True, slots=True)
class ConversationHistoryMaterializationRequest:
    db: AsyncSession
    actor: CurrentUser
    agent_id: UUID
    conversation_id: UUID
    confirmation_name: str
    checkpointer: BaseCheckpointSaver[str]


@dataclass(frozen=True, slots=True)
class CheckpointProjection:
    checkpoint_id: str
    parent_checkpoint_id: str | None
    checkpoint_namespace: str
    channel_values: dict[str, JsonValue]
    messages: list[JsonValue]
    branch: dict[str, JsonValue]
    trace: dict[str, JsonValue]
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class EventProjection:
    assistant_message_id: str
    events: list[JsonValue]
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class HistoryProjection:
    checkpoints: tuple[CheckpointProjection, ...]
    events: tuple[EventProjection, ...]
    canonical_payload_sha256: str


__all__ = [
    "CheckpointProjection",
    "ConversationHistoryMaterializationRequest",
    "EventProjection",
    "HistoryProjection",
]
