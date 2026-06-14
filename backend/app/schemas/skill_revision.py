from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.schemas.skill import SkillResponse
from app.schemas.skill_builder import JsonValue


class SkillRevisionOperation(StrEnum):
    CREATE = "create"
    MANUAL_METADATA_UPDATE = "manual_metadata_update"
    MANUAL_CONTENT_UPDATE = "manual_content_update"
    MANUAL_FILE_UPDATE = "manual_file_update"
    BUILDER_CREATE = "builder_create"
    BUILDER_IMPROVEMENT = "builder_improvement"
    ROLLBACK = "rollback"


class SkillRevisionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    skill_id: uuid.UUID
    revision_number: int
    operation: SkillRevisionOperation
    skill_version: str | None = None
    content_hash: str | None = None
    size_bytes: int = 0
    file_count: int = 0
    changelog_summary: str | None = None
    created_at: datetime


class SkillRevisionDetail(SkillRevisionSummary):
    changed_files: list[JsonValue] | None = None
    changelog_items: list[JsonValue] | None = None
    compatibility_result: dict[str, JsonValue] | None = None
    evaluation_summary: dict[str, JsonValue] | None = None
    metadata_json: dict[str, JsonValue]


class SkillRollbackResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    skill: SkillResponse
    revision: SkillRevisionSummary
