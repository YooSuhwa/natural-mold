"""S30 domains, SSO policy and the email-first login contract (plan 9.7, 10.10)."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class TenantDomainCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: Annotated[str, Field(min_length=3, max_length=253)]


class TenantDomainRead(BaseModel):
    id: UUID
    domain: str
    status: Literal["pending", "verified", "disabled"]
    txt_record: str
    verified_at: datetime | None
    created_at: datetime


class TenantDomainList(BaseModel):
    items: list[TenantDomainRead]


class SsoPolicyRead(BaseModel):
    mode: Literal["off", "optional", "required"]
    break_glass_user_ids: list[UUID]
    password_members: int = Field(
        description="Active members who sign in with a password and are not break-glass"
    )


class SsoPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["off", "optional", "required"]
    break_glass_user_ids: list[UUID] = Field(default_factory=list, max_length=2)


class SsoDiscoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[str, Field(min_length=3, max_length=255)]


class SsoLoginOption(BaseModel):
    id: UUID
    protocol: Literal["oidc", "ldap"]
    name: str
    start_url: str | None


class SsoDiscoverResponse(BaseModel):
    method: Literal["password", "sso"]
    sso_required: bool
    connections: list[SsoLoginOption]
    login_csrf: str


class LdapLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Annotated[str, Field(min_length=1, max_length=256)]
    password: SecretStr
