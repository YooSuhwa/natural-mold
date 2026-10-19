from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Mapped

from app.schemas.conversation_run import UtcDatetime


class ConversationRefSource(Protocol):
    """Stable conversation fields accepted by the router projection boundary."""

    @property
    def id(self) -> uuid.UUID | Mapped[uuid.UUID]: ...

    @property
    def agent_id(self) -> uuid.UUID | None | Mapped[uuid.UUID | None]: ...

    @property
    def active_branch_checkpoint_id(self) -> str | None | Mapped[str | None]: ...

    @property
    def queue_paused(self) -> bool | Mapped[bool]: ...

    @property
    def updated_at(self) -> datetime | Mapped[datetime]: ...


@dataclass(frozen=True, slots=True)
class ConversationRef:
    id: uuid.UUID
    agent_id: uuid.UUID
    active_branch_checkpoint_id: str | None
    queue_paused: bool
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationHistoryRef:
    """Conversation-owned identity that remains valid after Agent purge."""

    id: uuid.UUID
    agent_id: uuid.UUID | None
    agent_name: str
    lifecycle_state: Literal["active", "archived", "purged"]
    archived_at: UtcDatetime | None
    purged_at: UtcDatetime | None
    read_only: bool
    title: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime


class _ConversationRefPayload(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    active_branch_checkpoint_id: str | None
    queue_paused: bool
    updated_at: datetime


def conversation_ref(value: ConversationRefSource) -> ConversationRef:
    """Copy the stable fields routers need without importing the ORM model."""

    parsed = _ConversationRefPayload.model_validate(value)
    return ConversationRef(
        id=parsed.id,
        agent_id=parsed.agent_id,
        active_branch_checkpoint_id=parsed.active_branch_checkpoint_id,
        queue_paused=parsed.queue_paused,
        updated_at=parsed.updated_at,
    )


type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


__all__ = [
    "ConversationHistoryRef",
    "ConversationRef",
    "ConversationRefSource",
    "JsonValue",
    "conversation_ref",
]
