from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


# ---- Model test surface ----------------------------------------------------
#
# The ``test`` and ``test-preview`` endpoints share a response shape so the UI
# can reuse the same renderer between "registered model" and "before save"
# flows. ``raw_request`` always carries a redacted Authorization value; the
# real key never lands in the JSON payload.


class ModelTestPreviewRequest(BaseModel):
    """Body for ``POST /api/models/test-preview``.

    The model row may not exist yet (Custom ID / Discover preview), so the
    caller passes the wire shape inline plus a stored Credential reference.
    """

    model_config = ConfigDict(protected_namespaces=())

    provider: str = Field(..., description="Canonical provider key.")
    model_name: str = Field(..., description="Wire model id sent to the provider.")
    base_url: str | None = Field(
        default=None, description="Override; falls back to provider default."
    )
    credential_id: uuid.UUID = Field(
        ..., description="Stored Credential whose decrypted payload supplies the API key."
    )


class ModelTestErrorSchema(BaseModel):
    kind: Literal["auth", "not_found", "rate_limit", "timeout", "other"]
    message: str
    raw: str | None = None


class ModelTestResponse(BaseModel):
    """Mirror of ``app.services.model_test.ModelTestResult``."""

    success: bool
    response: str | None = None
    latency_ms: int = 0
    tokens_in: int | None = None
    tokens_out: int | None = None
    estimated_cost_usd: float | None = None
    error: ModelTestErrorSchema | None = None
    raw_request: dict[str, Any] | None = None
    raw_response: dict[str, Any] | None = None
    curl_command: str | None = None
    metadata: dict[str, Any] | None = None
