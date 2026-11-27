from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

ChildTaskStatus = Literal["running", "awaiting_approval", "completed", "cancelled", "failed"]


class ChildInstructionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


class ChildInstructionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    client_request_id: str
    text: str
    status: Literal["pending", "delivering", "applied", "cancelled", "review_required"]
    created_at: datetime
    applied_at: datetime | None


class ChildTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    tool_call_id: str
    role: str
    child_agent_id: UUID
    brief: str | None
    status: ChildTaskStatus
    checkpointed: bool
    created_at: datetime
    updated_at: datetime
    result: str | None
    instructions: list[ChildInstructionResponse]


class ChildTaskPage(BaseModel):
    items: list[ChildTaskResponse]
    next_offset: int | None
    enabled: bool
    allow_legacy_details: bool
