"""Skill API schemas — text and package kinds with metadata + file listings."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SkillCreate(BaseModel):
    """Create a text-kind skill via JSON.

    Package-kind skills are uploaded as multipart files to
    ``POST /api/skills/upload``.
    """

    name: str = Field(..., min_length=1, max_length=150)
    slug: str | None = None
    description: str | None = None
    content: str
    version: str | None = None


class SkillMetadataUpdate(BaseModel):
    """Patch metadata fields only — content edits use a separate endpoint."""

    name: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = None
    version: str | None = None


class SkillContentUpdate(BaseModel):
    content: str


class SkillFileUpdate(BaseModel):
    """PUT body for setting a single file inside a package skill."""

    content: str = Field(..., description="UTF-8 text body. Binary files use upload endpoint.")


class SkillFileEntry(BaseModel):
    path: str
    size: int
    is_dir: bool


class SkillResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    kind: Literal["text", "package"]
    version: str | None
    storage_path: str | None
    content_hash: str | None
    size_bytes: int
    used_by_count: int
    package_metadata: dict[str, Any] | None
    last_modified_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SkillBrief(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    kind: Literal["text", "package"]
    description: str | None

    model_config = {"from_attributes": True}


class SkillTextContentResponse(BaseModel):
    content: str
