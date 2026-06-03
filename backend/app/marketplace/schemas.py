"""Pydantic schemas — marketplace catalog/install/publish API contracts.

Per ``docs/design-docs/marketplace-module-contracts.md`` §3 + Spec §10.8.

Leaf module — must not import from any other ``app.marketplace`` module.
Only standard typing + ``app.models`` (forward refs) allowed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Origin / publication / installation summaries (embedded in many responses)
# ---------------------------------------------------------------------------


class ResourceOriginSummaryOut(BaseModel):
    """Where the current user's installed resource came from."""

    kind: Literal[
        "created_by_me",
        "imported_by_me",
        "built_in_k_skill",
        "shared_with_me",
        "community",
        "system_seed",
    ]
    label: str
    source_name: str | None = None
    source_user_id: uuid.UUID | None = None
    marketplace_item_id: uuid.UUID | None = None
    marketplace_version_id: uuid.UUID | None = None


class ResourcePublicationSummaryOut(BaseModel):
    """Whether/how the current user has published this resource."""

    state: Literal[
        "not_published",
        "draft",
        "published_private",
        "published_restricted",
        "published_public_listed",
        "published_public_unlisted",
        "published_unlisted",
        "disabled",
    ]
    item_id: uuid.UUID | None = None
    visibility: Literal[
        "private", "restricted", "public", "unlisted", "system"
    ] | None = None
    status: Literal["draft", "published", "deprecated", "disabled"] | None = None
    is_listed: bool = False
    latest_version_id: uuid.UUID | None = None
    version_number: int | None = None
    shared_user_count: int = 0


class MarketplaceInstallationSummary(BaseModel):
    """Is the current user installed on this item?"""

    installed: bool
    installation_id: uuid.UUID | None = None
    installed_resource_id: uuid.UUID | None = None
    status: Literal["active", "needs_setup", "disabled", "uninstalled"] | None = None
    update_available: bool = False
    dirty: bool = False


class CredentialSummaryOut(BaseModel):
    status: Literal["none", "optional", "required", "hosted_proxy", "manual_login"]
    required_count: int = 0
    optional_count: int = 0
    missing_required_count: int = 0


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------


class MarketplaceVersionSummary(BaseModel):
    id: uuid.UUID
    version_label: str
    version_number: int
    content_hash: str
    source_commit: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MarketplaceVersionDetail(BaseModel):
    """Full version payload (Spec §10.2). For ``GET /versions/{id}``."""

    id: uuid.UUID
    item_id: uuid.UUID
    version_label: str
    version_number: int
    resource_type: Literal["agent", "mcp", "skill"]
    payload_kind: Literal["skill_package", "agent_spec", "mcp_template"]
    payload: dict[str, Any] = Field(default_factory=dict)
    content_hash: str
    size_bytes: int
    credential_requirements: list[dict[str, Any]] | None = None
    dependency_requirements: list[dict[str, Any]] | None = None
    execution_profile: dict[str, Any] | None = None
    release_notes: str | None = None
    source_commit: str | None = None
    source_ref: str | None = None
    source_path: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Item
# ---------------------------------------------------------------------------


class MarketplaceItemOut(BaseModel):
    """Catalog row + detail response (Spec §10.8).

    Read-only side. Service layer constructs this via ``derive_*`` helpers
    so the ORM/Pydantic mapping never leaks privileged columns
    (moderation_status, ACL membership).
    """

    id: uuid.UUID
    resource_type: Literal["agent", "mcp", "skill"]
    name: str
    slug: str
    description: str | None = None
    icon_id: str | None = None
    icon_url: str | None = None
    visibility: Literal["private", "restricted", "public", "unlisted", "system"]
    status: Literal["draft", "published", "deprecated", "disabled"]
    is_system: bool
    is_listed: bool
    tags: list[str] | None = None
    categories: list[str] | None = None
    locale: str | None = None
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None
    latest_version: MarketplaceVersionSummary | None = None
    credential_summary: CredentialSummaryOut
    execution_profile: dict[str, Any] | None = None
    origin_summary: ResourceOriginSummaryOut | None = None
    publication_summary: ResourcePublicationSummaryOut
    installation: MarketplaceInstallationSummary
    # owner / super_user 시점에만 채워진다 (frontend 가 ACL revoke UI 에서
    # 사용). 다른 user 응답에서는 None — list 자체가 leak 되면 enumeration
    # oracle 위반.
    acl_user_ids: list[uuid.UUID] | None = None

    model_config = ConfigDict(from_attributes=True)


class MarketplaceItemsPage(BaseModel):
    """Offset page envelope for catalog screens that must not load all rows."""

    items: list[MarketplaceItemOut]
    limit: int
    offset: int
    total: int | None = None
    has_more: bool
    next_offset: int | None = None


# ---------------------------------------------------------------------------
# Credential requirements
# ---------------------------------------------------------------------------


class CredentialRequirementOut(BaseModel):
    """Public-facing view of a version's credential requirement entry."""

    key: str
    definition_key: str
    required: bool
    label: str
    description: str | None = None
    fields: list[str] = Field(default_factory=list)
    injection: Literal["env", "config"] = "env"
    scope: Literal["user", "system_dependency", "manual"] = "user"


class CredentialRequirementIn(CredentialRequirementOut):
    """Publish/k-skill import input. Adds env_map projection."""

    env_map: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Install / update
# ---------------------------------------------------------------------------


class InstallMarketplaceItemIn(BaseModel):
    version_id: uuid.UUID | None = None
    name_override: str | None = None
    credential_bindings: dict[str, uuid.UUID] = Field(default_factory=dict)
    install_missing_credentials: Literal["reject", "needs_setup"] = "needs_setup"
    install_mode: Literal[
        "reuse_or_update", "new_copy", "overwrite_existing"
    ] = "reuse_or_update"


class UpdateMarketplaceInstallationIn(BaseModel):
    strategy: Literal["overwrite", "install_new_copy", "keep_current"]


class MarketplaceInstallationOut(BaseModel):
    """Full installation row response (Spec §10.3)."""

    id: uuid.UUID
    item_id: uuid.UUID
    version_id: uuid.UUID
    resource_type: Literal["agent", "mcp", "skill"]
    installed_skill_id: uuid.UUID | None = None
    installed_agent_id: uuid.UUID | None = None
    installed_mcp_server_id: uuid.UUID | None = None
    install_status: Literal["active", "needs_setup", "disabled", "uninstalled"]
    is_dirty: bool
    installed_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Publish (Slice C; declared here so origin_service / routers can reference)
# ---------------------------------------------------------------------------


class PublishSkillIn(BaseModel):
    item_id: uuid.UUID | None = None
    visibility: Literal["private", "restricted", "public", "unlisted"]
    name: str
    description: str | None = None
    icon_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    release_notes: str | None = None
    credential_requirements: list[CredentialRequirementIn] = Field(default_factory=list)
    acl_user_ids: list[uuid.UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_acl(self) -> PublishSkillIn:
        if self.visibility == "restricted" and not self.acl_user_ids:
            # Match the error_codes ``marketplace_acl_required`` (400).
            raise ValueError("marketplace_acl_required")
        return self


class MarketplaceItemPatchIn(BaseModel):
    """Metadata-only PATCH (no version mutation).

    ``visibility`` 는 새 version 을 만들지 않는 경량 전환 — public ↔ private
    ↔ unlisted 토글 (예: 사용자가 잘못 public 으로 publish 한 것을 private
    으로 되돌리는 경로). ``restricted`` 로 바꾸려면 ACL endpoint 도 함께
    호출해야 한다 (ACL 비어 있으면 ``marketplace_acl_required``).
    """

    name: str | None = None
    description: str | None = None
    icon_id: str | None = None
    icon_url: str | None = None
    tags: list[str] | None = None
    categories: list[str] | None = None
    locale: str | None = None
    visibility: Literal["private", "restricted", "public", "unlisted"] | None = None


class MarketplaceItemAdminListedIn(BaseModel):
    is_listed: bool


class MarketplaceVersionFromSkillIn(BaseModel):
    """Body for ``POST /items/{item_id}/versions/from-skill/{skill_id}``."""

    release_notes: str | None = None


class MarketplaceItemACLIn(BaseModel):
    """Body for ``POST /items/{item_id}/acl``."""

    user_ids: list[uuid.UUID] = Field(default_factory=list)
    permission: Literal["view", "install", "manage"] = "install"


# ---------------------------------------------------------------------------
# Catalog filters
# ---------------------------------------------------------------------------


class MarketplaceItemListFilters(BaseModel):
    """Server-side filter projection from query params (Spec §10.1)."""

    resource_type: Literal["agent", "mcp", "skill"] | None = None
    q: str | None = None
    visibility: list[Literal["private", "restricted", "public", "unlisted", "system"]] | None = None
    category: list[str] | None = None
    installed: bool | None = None
    install_state: Literal["active", "needs_setup", "disabled", "uninstalled"] | None = None
    support_level: str | None = None
    source_kind: str | None = None
    is_listed: bool | None = None


__all__ = [
    "CredentialRequirementIn",
    "CredentialRequirementOut",
    "CredentialSummaryOut",
    "InstallMarketplaceItemIn",
    "MarketplaceInstallationSummary",
    "MarketplaceItemACLIn",
    "MarketplaceItemAdminListedIn",
    "MarketplaceItemListFilters",
    "MarketplaceItemOut",
    "MarketplaceItemsPage",
    "MarketplaceItemPatchIn",
    "MarketplaceVersionFromSkillIn",
    "MarketplaceVersionDetail",
    "MarketplaceVersionSummary",
    "PublishSkillIn",
    "ResourceOriginSummaryOut",
    "ResourcePublicationSummaryOut",
    "UpdateMarketplaceInstallationIn",
]
