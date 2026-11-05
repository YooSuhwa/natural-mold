"""Pydantic schemas for the guardrail model service settings (guardrail-minimum-plan.md §2.1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class GuardrailModelSettingOut(BaseModel):
    """What the ``kr_model`` pack uses now, and where it comes from."""

    enabled: bool
    url: str
    # "environment": nothing saved, GUARDRAIL_PII_MODEL_URL applies; "saved": the row below.
    source: Literal["environment", "saved"]
    environment_url: str
    credential_id: uuid.UUID | None = None
    credential_name: str | None = None
    updated_at: datetime | None = None


class GuardrailModelSettingUpdate(BaseModel):
    enabled: bool
    url: str = Field(default="", max_length=2048)
    # A system ``http_bearer`` credential; None sends GUARDRAIL_PII_MODEL_API_KEY, if set.
    credential_id: uuid.UUID | None = None


class GuardrailModelProbeRequest(BaseModel):
    """Test an address before saving it; the form sends what it shows."""

    url: str = Field(max_length=2048)
    credential_id: uuid.UUID | None = None


class GuardrailModelProbeOut(BaseModel):
    status: Literal["ok", "unreachable", "timeout", "unauthorized", "model_missing", "bad_response"]
    latency_ms: int | None = None
    model: str | None = None
    findings: int | None = None
