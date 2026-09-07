from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ConversationRef:
    id: uuid.UUID
    agent_id: uuid.UUID
    active_branch_checkpoint_id: str | None
    queue_paused: bool
    updated_at: datetime


def conversation_ref(value: object) -> ConversationRef:
    """Copy the stable fields routers need without importing the ORM model."""

    conversation_id = getattr(value, "id", None)
    agent_id = getattr(value, "agent_id", None)
    active_checkpoint = getattr(value, "active_branch_checkpoint_id", None)
    queue_paused = getattr(value, "queue_paused", None)
    updated_at = getattr(value, "updated_at", None)
    if (
        not isinstance(conversation_id, uuid.UUID)
        or not isinstance(agent_id, uuid.UUID)
        or not (active_checkpoint is None or isinstance(active_checkpoint, str))
        or not isinstance(queue_paused, bool)
        or not isinstance(updated_at, datetime)
    ):
        raise TypeError("Conversation reference is incomplete")
    return ConversationRef(
        id=conversation_id,
        agent_id=agent_id,
        active_branch_checkpoint_id=active_checkpoint,
        queue_paused=queue_paused,
        updated_at=updated_at,
    )


type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


__all__ = ["ConversationRef", "JsonValue", "conversation_ref"]
