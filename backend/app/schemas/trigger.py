from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class TriggerCreate(BaseModel):
    trigger_type: str  # "interval" | "cron"
    schedule_config: dict[str, Any]  # {"interval_minutes": 10} or {"cron_expression": "0 9 * * *"}
    input_message: str


class TriggerUpdate(BaseModel):
    trigger_type: str | None = None
    schedule_config: dict[str, Any] | None = None
    input_message: str | None = None
    status: str | None = None  # "active" | "paused"


class TriggerResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    trigger_type: str
    schedule_config: dict[str, Any]
    input_message: str
    status: str
    last_run_at: datetime | None
    next_run_at: datetime | None
    run_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
