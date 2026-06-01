"""Skill API schemas — text and package kinds with metadata + file listings.

ADR-017 Slice A: ``SkillResponse`` carries origin / publication / installation
summaries so the frontend can render badges (created vs imported, published
state, dirty/update-available) without a second round-trip per skill.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.marketplace.schemas import (
    MarketplaceInstallationSummary,
    ResourceOriginSummaryOut,
    ResourcePublicationSummaryOut,
)


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
    execution_profile: dict[str, Any] | None = None
    last_modified_at: datetime
    created_at: datetime
    updated_at: datetime

    # ADR-017 Slice A — origin/publication/installation derivation
    # populated by the router via ``origin_service.derive_*``. ``origin``
    # is always populated; ``publication`` defaults to ``not_published``;
    # ``installation`` defaults to ``installed=False``.
    origin_summary: ResourceOriginSummaryOut | None = None
    publication_summary: ResourcePublicationSummaryOut | None = None
    installation: MarketplaceInstallationSummary | None = None

    model_config = {"from_attributes": True}


class SkillBrief(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    kind: Literal["text", "package"]
    description: str | None
    execution_profile: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class SkillTextContentResponse(BaseModel):
    content: str


# ---------------------------------------------------------------------------
# Credential binding API (ADR-017 Slice D)
# ---------------------------------------------------------------------------


class SkillCredentialRequirementOut(BaseModel):
    """One ``credential_requirements`` entry stored on the skill row.

    Mirror of ``app.marketplace.schemas.CredentialRequirementOut`` but kept
    here so ``app.schemas.skill`` does not import from ``app.marketplace``
    (avoid a cycle when the marketplace schemas evolve)."""

    key: str
    definition_key: str
    required: bool
    label: str
    description: str | None = None
    fields: list[str] = Field(default_factory=list)
    injection: Literal["env", "config"] = "env"
    scope: Literal["user", "system_dependency", "manual"] = "user"


class SkillCredentialBindingOut(BaseModel):
    """An existing binding row."""

    id: uuid.UUID
    requirement_key: str
    credential_id: uuid.UUID
    scope: Literal["skill", "agent_skill"]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SkillCredentialBindingIn(BaseModel):
    credential_id: uuid.UUID
