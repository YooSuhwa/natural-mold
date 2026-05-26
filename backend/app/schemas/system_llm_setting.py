"""Pydantic schemas for System LLM settings (ADR-019)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class SystemLlmSettingOut(BaseModel):
    """A single role slot's resolved configuration for the operator screen."""

    role: str
    credential_id: uuid.UUID | None = None
    credential_name: str | None = None
    # Derived from credential.definition_key (single source of truth).
    provider: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    # True when both credential and model are selected (slot is usable).
    configured: bool = False
    updated_at: datetime


class SystemLlmSettingUpdate(BaseModel):
    """PUT body — selects (or clears) the credential/model for a role."""

    credential_id: uuid.UUID | None = None
    model_name: str | None = None
