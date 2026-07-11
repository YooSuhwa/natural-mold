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
    parent_revision_id: uuid.UUID | None = None
    restored_from_revision_id: uuid.UUID | None = None
    changed_files: list[JsonValue] | None = None
    changelog_items: list[JsonValue] | None = None
    compatibility_result: dict[str, JsonValue] | None = None
    evaluation_summary: dict[str, JsonValue] | None = None
    metadata_json: dict[str, JsonValue]


class SkillRollbackResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    skill: SkillResponse
    revision: SkillRevisionSummary


class SkillRevisionFileEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    size: int
    # 앞 8KB sniff에 널바이트가 있는 파일 — 내용 조회는 404(fail-closed).
    is_binary: bool


class SkillRevisionFilesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    # 리텐션이 스냅샷을 정리한 리비전 — 파일 목록/내용을 제공할 수 없다.
    snapshot_pruned: bool
    files: list[SkillRevisionFileEntry]


class SkillRevisionFileContentResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    content: str
