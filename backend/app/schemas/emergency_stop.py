"""Emergency stop shapes (feature plan v2 순번 13, 17.4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class EmergencyStopCreate(BaseModel):
    scope_type: Literal["agent", "user", "mcp_server"]
    scope_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=500)


class EmergencyStopRelease(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class EmergencyStopState(BaseModel):
    block_received: bool
    new_calls_blocked: bool
    runs_cancel_requested: int
    runs_cancel_confirmed: int
    schedules_paused: int
    deployments_disabled: int
    approvals_canceled: int
    external_side_effects: Literal["unknown"]


class EmergencyStopView(BaseModel):
    id: uuid.UUID
    scope_type: str
    scope_id: uuid.UUID
    scope_name: str
    status: str
    reason: str
    stopped_by: uuid.UUID | None
    stopped_at: datetime
    released_by: uuid.UUID | None
    released_at: datetime | None
    release_reason: str | None
    state: EmergencyStopState


class EmergencyStopPage(BaseModel):
    items: list[EmergencyStopView]
