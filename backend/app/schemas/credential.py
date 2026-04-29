"""Pydantic schemas for the credential API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.markers import check_reserved_marker

# ---- Catalog ---------------------------------------------------------------


class CredentialFieldSchema(BaseModel):
    name: str
    display_name: str
    kind: str
    default: Any = None
    required: bool = False
    description: str | None = None
    options: list[dict[str, Any]] = Field(default_factory=list)
    placeholder: str | None = None
    type_options: dict[str, Any] = Field(default_factory=dict)
    display_options: dict[str, Any] = Field(default_factory=dict)


class CredentialDefinitionSchema(BaseModel):
    key: str
    display_name: str
    icon_id: str | None = None
    documentation_url: str | None = None
    category: str = "general"
    extends: list[str] = Field(default_factory=list)
    properties: list[CredentialFieldSchema] = Field(default_factory=list)
    has_test: bool = False
    has_oauth: bool = False


# ---- CRUD ------------------------------------------------------------------


class CredentialCreate(BaseModel):
    definition_key: str
    name: str
    data: dict[str, Any]
    is_shared: bool = False

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        check_reserved_marker(value, "name")
        return value

    def normalized_name(self) -> str:
        return self.name


class CredentialUpdate(BaseModel):
    name: str | None = None
    data: dict[str, Any] | None = None
    is_shared: bool | None = None
    status: Literal["active", "disabled", "expired"] | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        return check_reserved_marker(value, "name")


class CredentialResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    definition_key: str
    name: str
    field_keys: list[str]
    is_shared: bool
    status: str
    key_id: str
    last_used_at: datetime | None = None
    last_tested_at: datetime | None = None
    last_test_result: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


# ---- Test ------------------------------------------------------------------


class CredentialTestResponse(BaseModel):
    success: bool
    http_status: int | None = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class PreviewTestRequest(BaseModel):
    definition_key: str
    data: dict[str, Any]


# ---- Audit log -------------------------------------------------------------


class CredentialAuditLogResponse(BaseModel):
    id: uuid.UUID
    credential_id: uuid.UUID
    actor_user_id: uuid.UUID | None = None
    action: str
    source: str
    ip: str | None = None
    user_agent: str | None = None
    error: str | None = None
    log_metadata: dict[str, Any] | None = None
    created_at: datetime


# ---- OAuth2 helpers --------------------------------------------------------


class OAuth2AuthStartResponse(BaseModel):
    authorization_url: str
    state: str
