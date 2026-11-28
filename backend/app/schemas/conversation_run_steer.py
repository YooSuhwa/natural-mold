from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

SteerText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=12000, pattern=r"^[^\x00]*$"),
]


class ConversationRunSteerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    text: SteerText


class ConversationRunSteerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    conversation_id: UUID
    client_request_id: UUID
    sequence: int
    text: str
    status: Literal["pending", "applied", "queued", "needs_review"]
    created_at: datetime
    applied_at: datetime | None
    queued_input_id: UUID | None
