from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.conversation_goal_criteria import GoalCriterion, GoalEvidence


class GoalWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    criteria: list[GoalCriterion] = Field(min_length=1, max_length=12)


class GoalActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    action: Literal["pause", "resume", "cancel"]


class GoalDetails(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    criteria: list[GoalCriterion]
    version: int
    revision: int
    status: Literal["active", "paused", "blocked", "completed", "canceled"]
    auto_followups: int
    blocked_reason: str | None
    last_evidence: GoalEvidence | None


class GoalResponse(BaseModel):
    enabled: bool
    goal: GoalDetails | None
