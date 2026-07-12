"""휴먼 피드백 스키마 (Phase 3 §7, D2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SkillFeedbackUpsertRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=2000)


class SkillFeedbackMineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    rating: str
    comment: str | None = None
    updated_at: datetime


class SkillFeedbackSummaryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    skill_id: uuid.UUID
    up_count: int = Field(..., ge=0)
    down_count: int = Field(..., ge=0)
    mine: SkillFeedbackMineResponse | None = None


class SkillCaseFeedbackUpsertRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_index: int = Field(..., ge=0)
    verdict: Literal["agree", "disagree"]
    comment: str | None = Field(default=None, max_length=2000)


class SkillCaseFeedbackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    run_id: uuid.UUID
    case_index: int
    verdict: str
    comment: str | None = None
    updated_at: datetime
