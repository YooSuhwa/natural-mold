"""Shared operating memo of an agent and its proposals (plan 11.8, 9.7; F-09)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.memory import (
    MAX_MEMORY_CONTENT_LENGTH,
    MAX_MEMORY_REASON_LENGTH,
    MemoryProposalStatus,
)

MAX_AGENT_MEMO_LENGTH = 20000
# Shown when proposing: the agent's editors read the proposed text (11.8).
MEMO_PROPOSAL_AUDIENCE: Literal["agent_editors"] = "agent_editors"


def _strip(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be empty")
    return stripped


class AgentMemoOut(BaseModel):
    agent_id: uuid.UUID
    content: str
    enabled: bool  # served to runs (11.8 per-agent switch; new agents start off)


class AgentMemoUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = Field(default=None, max_length=MAX_AGENT_MEMO_LENGTH)
    enabled: bool | None = None

    @model_validator(mode="after")
    def _something(self) -> AgentMemoUpdate:
        if self.content is None and self.enabled is None:
            raise ValueError("content or enabled is required")
        return self


class AgentMemoProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=MAX_MEMORY_CONTENT_LENGTH)
    reason: str | None = Field(default=None, max_length=MAX_MEMORY_REASON_LENGTH)
    conversation_id: uuid.UUID | None = None

    _clean = field_validator("content", "reason")(_strip)


class AgentMemoProposalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    content: str | None = Field(default=None, min_length=1, max_length=MAX_MEMORY_CONTENT_LENGTH)

    _clean = field_validator("content")(_strip)


class AgentMemoProposalOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    agent_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    content: str
    reason: str | None = None
    status: MemoryProposalStatus
    created_at: datetime
    resolved_at: datetime | None = None
    visible_to: Literal["agent_editors"] = MEMO_PROPOSAL_AUDIENCE

    model_config = ConfigDict(from_attributes=True)
