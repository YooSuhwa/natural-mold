"""Approval inbox shapes (feature plan v2 순번 13, 17.2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApprovalAction(BaseModel):
    name: str
    args: Any = None
    description: str | None = None
    allowed_decisions: list[str] = Field(default_factory=list)


class ApprovalView(BaseModel):
    """What the decider needs to decide; never the conversation itself."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    source: str
    agent_id: uuid.UUID | None
    agent_name: str
    conversation_id: uuid.UUID
    trigger_id: uuid.UUID | None
    actions: list[ApprovalAction]
    attempt: int
    policy_version: int | None
    channel: str | None
    reason: str | None
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None


class ApprovalPage(BaseModel):
    items: list[ApprovalView]


class PendingCount(BaseModel):
    count: int


class EditedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    args: dict[str, Any]


class InboxDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["approve", "edit", "reject"]
    edited_action: EditedAction | None = None
    message: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _edit_has_args(self) -> InboxDecision:
        if (self.type == "edit") != (self.edited_action is not None):
            raise ValueError("edited_action goes with an edit decision only")
        return self


class InboxDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[InboxDecision] = Field(min_length=1, max_length=20)


class RunStatusView(BaseModel):
    """One run in the run status tab (17.2): no way into another person's conversation."""

    model_config = ConfigDict(from_attributes=True)

    run_id: uuid.UUID
    state: Literal["running", "waiting", "failed"]
    status: str
    agent_id: uuid.UUID | None
    agent_name: str
    source: str | None
    mine: bool
    conversation_id: uuid.UUID | None
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None


class RunStatusPage(BaseModel):
    items: list[RunStatusView]
