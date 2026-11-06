"""Background job views (app/jobs)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

JobState = Literal["queued", "running", "succeeded", "failed", "canceled"]


class JobOut(BaseModel):
    id: uuid.UUID
    type: str
    resource_id: str | None
    state: JobState
    progress: int
    progress_note: str | None
    attempts: int
    max_attempts: int
    cancel_requested: bool
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
