from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class TemplateResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    category: str
    system_prompt: str
    recommended_tools: list[str] | None
    recommended_model_id: uuid.UUID | None
    usage_example: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
