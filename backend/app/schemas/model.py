from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class ModelCreate(BaseModel):
    """Payload for ``POST /api/models`` — register a discovered or custom model.

    Pricing fields are optional because catalog-less custom IDs are valid
    (``source='manual'``); the user can fill them in via PATCH later.
    """

    provider: str
    model_name: str
    display_name: str
    base_url: str | None = None
    is_default: bool = False
    cost_per_input_token: Decimal | None = None
    cost_per_output_token: Decimal | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None
    supports_vision: bool | None = None
    supports_function_calling: bool | None = None
    supports_reasoning: bool | None = None
    source: Literal["openrouter", "litellm", "manual"] | None = None


class ModelUpdate(BaseModel):
    """Patch a model — every field is optional. Pricing/meta override-friendly."""

    provider: str | None = None
    model_name: str | None = None
    display_name: str | None = None
    base_url: str | None = None
    is_default: bool | None = None
    cost_per_input_token: Decimal | None = None
    cost_per_output_token: Decimal | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None
    supports_vision: bool | None = None
    supports_function_calling: bool | None = None
    supports_reasoning: bool | None = None
    source: Literal["openrouter", "litellm", "manual"] | None = None


class ModelResponse(BaseModel):
    id: uuid.UUID
    provider: str
    model_name: str
    display_name: str
    base_url: str | None
    is_default: bool
    cost_per_input_token: Decimal | None
    cost_per_output_token: Decimal | None
    context_window: int | None = None
    max_output_tokens: int | None = None
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None
    supports_vision: bool | None = None
    supports_function_calling: bool | None = None
    supports_reasoning: bool | None = None
    source: str | None = None
    agent_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class DiscoveredModelSchema(BaseModel):
    """API surface mirror of ``app.services.model_discovery.DiscoveredModel``."""

    model_name: str
    display_name: str
    provider: str
    source: Literal["openrouter", "litellm", "manual"]
    context_window: int | None = None
    max_output_tokens: int | None = None
    cost_per_input_token: Decimal | None = None
    cost_per_output_token: Decimal | None = None
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None
    supports_vision: bool | None = None
    supports_function_calling: bool | None = None
    supports_reasoning: bool | None = None
    already_registered: bool = False


class ModelBulkItem(BaseModel):
    model_name: str
    display_name: str
    context_window: int | None = None
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None
    cost_per_input_token: Decimal | None = None
    cost_per_output_token: Decimal | None = None


class ModelBulkCreate(BaseModel):
    provider: str = Field(..., description="LLM provider key shared by every item.")
    models: list[ModelBulkItem]
