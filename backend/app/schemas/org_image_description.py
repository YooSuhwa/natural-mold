"""An organization explicitly authorizes one provider destination for image descriptions."""

from decimal import Decimal
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

ImageDescriptionProvider = Literal["openai", "openai_compatible", "openrouter"]


def https_base_url(value: str) -> str:
    if not value or any(ord(char) <= 32 for char in value) or "\\" in value:
        raise ValueError("invalid provider URL")
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL:
        raise ValueError("invalid provider URL") from None
    if (
        url.scheme != "https"
        or not url.host
        or url.userinfo
        or url.query
        or url.fragment
        or "?" in value
        or "#" in value
    ):
        raise ValueError("a credential-free HTTPS provider base URL is required")
    return str(url).rstrip("/")


class ImageDescriptionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: UUID
    provider: ImageDescriptionProvider
    base_url: str = Field(min_length=1, max_length=500)
    max_input_bytes: int = Field(default=4 * 1024 * 1024, ge=1, le=8 * 1024 * 1024)
    max_output_tokens: int = Field(default=256, ge=1, le=1024)
    timeout_seconds: int = Field(default=15, ge=1, le=60)
    max_cost_usd: Decimal = Field(default=Decimal("0.50"), gt=0, le=10, decimal_places=8)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return https_base_url(value)
