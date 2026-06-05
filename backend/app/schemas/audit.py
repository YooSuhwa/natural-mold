from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

AuditScope = Literal["mine", "all"]


class AuditEventResponse(BaseModel):
    id: uuid.UUID
    actor_type: str
    actor_user_id: uuid.UUID | None = None
    actor_api_key_id: uuid.UUID | None = None
    actor_email_snapshot: str | None = None
    actor_label: str | None = None
    owner_user_id: uuid.UUID | None = None
    owner_email_snapshot: str | None = None
    action: str
    target_type: str
    target_id: str | None = None
    target_name_snapshot: str | None = None
    target_owner_user_id: uuid.UUID | None = None
    outcome: str
    reason_code: str | None = None
    reason_message: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    run_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    metadata: dict[str, Any] | None = Field(default=None)
    created_at: datetime


class AuditEventPageResponse(BaseModel):
    items: list[AuditEventResponse]
    next_cursor: str | None = None
