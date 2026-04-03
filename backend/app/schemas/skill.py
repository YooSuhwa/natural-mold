from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, model_validator


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
    type: str = "text"
    has_scripts: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="wrap")
    @classmethod
    def _compute_has_scripts(cls, data: Any, handler: Any) -> SkillResponse:
        result = handler(data)
        # Check _has_scripts (set by upload) or compute from storage_path
        if hasattr(data, "_has_scripts"):
            result.has_scripts = data._has_scripts
        elif hasattr(data, "storage_path") and data.storage_path:
            scripts_dir = Path(data.storage_path) / "scripts"
            if scripts_dir.is_dir():
                result.has_scripts = any(scripts_dir.glob("*.py"))
        return result


class SkillBrief(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None

    model_config = {"from_attributes": True}
