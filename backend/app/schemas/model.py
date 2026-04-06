from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class ModelCreate(BaseModel):
    provider: str
    model_name: str
    display_name: str
    provider_id: uuid.UUID | None = None
    base_url: str | None = None
    api_key: str | None = None
    is_default: bool = False
    cost_per_input_token: Decimal | None = None
    cost_per_output_token: Decimal | None = None
    context_window: int | None = None
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None


class ModelUpdate(BaseModel):
    provider: str | None = None
    model_name: str | None = None
    display_name: str | None = None
    provider_id: uuid.UUID | None = None
    base_url: str | None = None
    api_key: str | None = None
    is_default: bool | None = None
    cost_per_input_token: Decimal | None = None
    cost_per_output_token: Decimal | None = None
    context_window: int | None = None
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None


class ModelResponse(BaseModel):
    id: uuid.UUID
    provider: str
    model_name: str
    display_name: str
    base_url: str | None
    is_default: bool
    cost_per_input_token: Decimal | None
    cost_per_output_token: Decimal | None
    provider_id: uuid.UUID | None = None
    provider_name: str | None = None
    context_window: int | None = None
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ModelBulkItem(BaseModel):
    model_name: str
    display_name: str
    context_window: int | None = None
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None
    cost_per_input_token: Decimal | None = None
    cost_per_output_token: Decimal | None = None


class ModelBulkCreate(BaseModel):
    provider_id: uuid.UUID
    models: list[ModelBulkItem]
