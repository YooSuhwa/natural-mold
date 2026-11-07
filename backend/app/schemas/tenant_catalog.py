"""Company settings tier: models, shared credentials, service LLM, catalog (9.7, S07; E-07)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.schemas.model import ModelCreate, ModelUpdate
from app.schemas.org_catalog import CatalogKind

SystemLlmRole = Literal["text_primary", "text_fallback", "image"]
OrgIds = list[uuid.UUID]


class TenantModelCreate(ModelCreate):
    """A company model (``models.tenant_id``) and the organizations it reaches."""

    allowed_org_ids: OrgIds = Field(default_factory=list, max_length=500)


class TenantModelUpdate(ModelUpdate):
    """Company models accept every field; platform models only ``allowed_org_ids``
    (``null`` = every organization of the company)."""

    allowed_org_ids: OrgIds | None = Field(default=None, max_length=500)


class TenantModelView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: uuid.UUID
    source: Literal["platform", "company"]
    provider: str
    model_name: str
    display_name: str
    base_url: str | None
    is_default: bool
    is_visible: bool
    default_credential_id: uuid.UUID | None
    company_enabled: bool  # platform → company tiers keep it
    requires_company_key: bool  # platform model while platform keys are not allowed
    allowed_org_ids: OrgIds | None  # None = every organization (platform models only)


class TenantModelList(BaseModel):
    model_config = ConfigDict(frozen=True)
    items: tuple[TenantModelView, ...]
    allow_system_models: bool


class TenantCredentialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definition_key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    data: dict[str, JsonValue]
    allowed_org_ids: OrgIds = Field(default_factory=list, max_length=500)


class TenantCredentialUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    data: dict[str, JsonValue] | None = None
    status: Literal["active", "disabled"] | None = None
    allowed_org_ids: OrgIds | None = Field(default=None, max_length=500)


class TenantCredentialView(BaseModel):
    """Values are never returned, only field names (S07)."""

    model_config = ConfigDict(frozen=True)
    id: uuid.UUID
    definition_key: str
    name: str
    field_keys: tuple[str, ...]
    status: str
    allowed_org_ids: OrgIds
    created_at: datetime
    updated_at: datetime


class TenantCatalogItemView(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: CatalogKind
    id: uuid.UUID
    name: str
    source: Literal["platform", "company"]
    platform_available: bool
    company_enabled: bool


class TenantCatalogPolicyView(BaseModel):
    model_config = ConfigDict(frozen=True)
    items: tuple[TenantCatalogItemView, ...]
    allow_system_models: bool
    updated_at: datetime


class TenantLlmSettingUpdate(BaseModel):
    """Both ``null`` drops the company override (inherit the platform default)."""

    model_config = ConfigDict(extra="forbid")
    role: SystemLlmRole
    credential_id: uuid.UUID | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=200)


class TenantLlmSettingView(BaseModel):
    model_config = ConfigDict(frozen=True)
    role: str
    inherits: bool  # no company override: the platform default applies (when allowed)
    credential_id: uuid.UUID | None
    credential_name: str | None
    provider: str | None
    model_name: str | None
    configured: bool  # the effective slot resolves to a credential and a model


class TenantLlmSettingsView(BaseModel):
    model_config = ConfigDict(frozen=True)
    items: tuple[TenantLlmSettingView, ...]
    allow_system_models: bool
