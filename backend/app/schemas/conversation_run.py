from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, PlainSerializer


def _utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.isoformat()


UtcDatetime = Annotated[datetime, PlainSerializer(_utc_iso, return_type=str, when_used="json")]


class ConversationRunResponse(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    agent_id: uuid.UUID
    parent_run_id: uuid.UUID | None = None
    status: str
    source: str
    worker_instance_id: str | None = None
    interrupt_id: str | None = None
    last_event_id: str | None = None
    input_preview: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    cancel_requested_at: UtcDatetime | None = None
    started_at: UtcDatetime | None = None
    heartbeat_at: UtcDatetime | None = None
    completed_at: UtcDatetime | None = None
    created_at: UtcDatetime
    updated_at: UtcDatetime

    model_config = {"from_attributes": True}
