from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.conversation import UtcDatetime

PINNED_SUMMARY_MAX_CHARS = 4000
PinnedSummarySourceStatus = Literal["current", "changed", "deleted", "other_branch"]


class PinConversationSummaryRequest(BaseModel):
    """Select an existing assistant message as the conversation summary."""

    message_id: str = Field(min_length=1, max_length=255)

    model_config = ConfigDict(frozen=True)


class PinnedConversationSummaryResponse(BaseModel):
    conversation_id: uuid.UUID
    source_message_id: str
    source_branch_checkpoint_id: str
    snapshot_text: str = Field(max_length=PINNED_SUMMARY_MAX_CHARS)
    source_status: PinnedSummarySourceStatus
    created_at: UtcDatetime
    updated_at: UtcDatetime

    model_config = ConfigDict(from_attributes=True, frozen=True)


class PinnedConversationSummaryEnvelope(BaseModel):
    summary: PinnedConversationSummaryResponse | None

    model_config = ConfigDict(frozen=True)
