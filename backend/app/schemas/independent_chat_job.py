from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class IndependentJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    target_id: UUID
    instruction: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16000)
    ]
    source_child_task_id: UUID | None = None


JobStatus = Literal[
    "queued",
    "running",
    "waiting_for_approval",
    "canceling",
    "canceled",
    "completed",
    "failed",
    "needs_review",
]


class IndependentJobOut(BaseModel):
    id: UUID
    parent_agent_id: UUID
    parent_conversation_id: UUID
    parent_run_id: UUID
    child_agent_id: UUID
    child_agent_name: str
    child_conversation_id: UUID
    child_run_id: UUID
    status: JobStatus
    reason_code: str | None = None


class IndependentTargetOut(BaseModel):
    id: UUID
    name: str


class IndependentJobsOut(BaseModel):
    enabled: bool
    parent_job: IndependentJobOut | None = None
    jobs: list[IndependentJobOut] = Field(default_factory=list)
    targets: list[IndependentTargetOut] = Field(default_factory=list)
