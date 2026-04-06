from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class ProviderCreate(BaseModel):
    name: str = Field(max_length=100)
    provider_type: Literal["openai", "anthropic", "google", "openrouter", "openai_compatible"]
    base_url: str | None = None
    api_key: str | None = None


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class ProviderResponse(BaseModel):
    id: uuid.UUID
    name: str
    provider_type: str
    base_url: str | None
    is_active: bool
    has_api_key: bool
    model_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProviderTestResponse(BaseModel):
    success: bool
    message: str
    models_count: int | None = None


class DiscoveredModel(BaseModel):
    model_name: str
    display_name: str
    context_window: int | None = None
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None
    cost_per_input_token: Decimal | None = None
    cost_per_output_token: Decimal | None = None
