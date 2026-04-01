from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class ModelCreate(BaseModel):
    provider: str
    model_name: str
    display_name: str
    base_url: str | None = None
    api_key: str | None = None
    is_default: bool = False
    cost_per_input_token: Decimal | None = None
    cost_per_output_token: Decimal | None = None


class ModelResponse(BaseModel):
    id: uuid.UUID
    provider: str
    model_name: str
    display_name: str
    base_url: str | None
    is_default: bool
    cost_per_input_token: Decimal | None
    cost_per_output_token: Decimal | None
    created_at: datetime

    model_config = {"from_attributes": True}
