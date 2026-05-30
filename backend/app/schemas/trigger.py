from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class TriggerCreate(BaseModel):
    name: str | None = None
    trigger_type: str  # "interval" | "cron" | "one_time"
    schedule_config: dict[str, Any]
    input_message: str
    timezone: str | None = None
    conversation_policy: str | None = None
    target_conversation_id: uuid.UUID | None = None
    max_runs: int | None = None
    end_at: datetime | None = None
    auto_pause_after_failures: int | None = None


class TriggerUpdate(BaseModel):
    name: str | None = None
    trigger_type: str | None = None
    schedule_config: dict[str, Any] | None = None
    input_message: str | None = None
    timezone: str | None = None
    conversation_policy: str | None = None
    target_conversation_id: uuid.UUID | None = None
    status: str | None = None  # "active" | "paused" | "completed" | "error"
    max_runs: int | None = None
    end_at: datetime | None = None
    auto_pause_after_failures: int | None = None


class TriggerResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    name: str
    trigger_type: str
    schedule_config: dict[str, Any]
    input_message: str
    timezone: str
    conversation_policy: str
    schedule_conversation_id: uuid.UUID | None
    target_conversation_id: uuid.UUID | None
    status: str
    last_run_at: datetime | None
    next_run_at: datetime | None
    last_status: str | None
    last_error: str | None
    run_count: int
    failure_count: int
    max_runs: int | None
    end_at: datetime | None
    auto_pause_after_failures: int | None
    created_at: datetime
    updated_at: datetime
    agent_name: str | None = None
    schedule_conversation_title: str | None = None
    schedule_conversation_unread_count: int = 0

    model_config = {"from_attributes": True}


class TriggerRunResponse(BaseModel):
    id: uuid.UUID
    trigger_id: uuid.UUID
    agent_id: uuid.UUID
    user_id: uuid.UUID
    conversation_id: uuid.UUID | None
    status: str
    input_message: str
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TriggerSummaryResponse(BaseModel):
    total_unread: int
    active_count: int
