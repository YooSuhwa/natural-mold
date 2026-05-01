"""Schemas for message feedback (P0-1c — thumbs up/down)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, PlainSerializer


def _utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.isoformat()


UtcDatetime = Annotated[datetime, PlainSerializer(_utc_iso, return_type=str, when_used="json")]

Rating = Literal["up", "down"]


class MessageFeedbackCreate(BaseModel):
    rating: Rating
    comment: str | None = None
    # Conversation id is required so we can scope the row + cascade-delete
    # alongside the conversation.
    conversation_id: uuid.UUID


class MessageFeedbackResponse(BaseModel):
    id: uuid.UUID
    message_id: str
    conversation_id: uuid.UUID
    rating: Rating
    comment: str | None
    created_at: UtcDatetime

    model_config = {"from_attributes": True}
