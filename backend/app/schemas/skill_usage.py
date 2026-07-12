"""Skill-axis usage summary schemas (Phase 3 spec §6)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class SkillUsageDailyPointResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    date: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    execution_count: int


class SkillUsageSummaryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    skill_id: uuid.UUID
    days: int
    tokens_in: int
    tokens_out: int
    # Sum of *known* costs only — events without pricing contribute nothing.
    cost_usd: float
    priced_event_count: int
    # Events that consumed tokens but had no pricing (cost unknown ≠ free).
    unpriced_token_event_count: int
    evaluation_run_count: int
    chat_execution_count: int
    daily: list[SkillUsageDailyPointResponse]
