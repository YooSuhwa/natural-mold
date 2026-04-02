from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class SkillCreate(BaseModel):
    name: str
    description: str | None = None
    content: str


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    content: str | None = None


class SkillResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    content: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SkillBrief(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None

    model_config = {"from_attributes": True}
