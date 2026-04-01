from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel


class TokenUsageResponse(BaseModel):
    agent_id: uuid.UUID
    period: str
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: Decimal

    model_config = {"from_attributes": True}


class AgentUsageRow(BaseModel):
    agent_id: uuid.UUID
    agent_name: str
    total_tokens: int
    estimated_cost: Decimal


class UsageSummaryResponse(BaseModel):
    period: str
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: Decimal
    by_agent: list[AgentUsageRow]
