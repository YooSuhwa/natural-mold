"""S30 SSO connection contract (plan 9.7 ``/api/tenants/{id}/sso-connections``).

Secrets are write-only: the read model reports ``has_secret`` and never echoes the
client secret or the bind password.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, field_validator

Role = Literal["member", "builder", "admin", "auditor"]
_Text = Annotated[str, Field(min_length=1, max_length=1000)]


def _https_issuer(value: str) -> str:
    value = value.strip()
    if not value.startswith("https://") or any(ch.isspace() for ch in value):
        raise ValueError("issuer must be an https:// URL")
    return value


def _user_filter(value: str) -> str:
    if "{username}" not in value:
        raise ValueError("user filter must contain {username}")
    return value


class _Common(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jit_enabled: bool = False
    default_role: Role = "member"
    # Without an ``email_verified`` claim (LDAP never sends one) the email is trusted for
    # attaching to an existing account only when this is on.
    trust_email_claim: bool = False
    group_role_mapping: dict[str, JsonValue] = Field(default_factory=dict)


class OidcConnectionCreate(_Common):
    protocol: Literal["oidc"]
    issuer: _Text
    client_id: Annotated[str, Field(min_length=1, max_length=500)]
    client_secret: SecretStr
    scopes: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=lambda: ["openid", "email", "profile"], max_length=20
    )
    claim_mapping: dict[str, str] = Field(default_factory=dict)

    _issuer = field_validator("issuer")(_https_issuer)


class LdapConnectionCreate(_Common):
    protocol: Literal["ldap"]
    server_url: _Text
    start_tls: bool = False
    bind_dn: _Text
    bind_password: SecretStr
    user_base_dn: _Text
    user_filter: _Text
    group_base_dn: str | None = Field(default=None, max_length=1000)
    group_filter: str | None = Field(default=None, max_length=1000)
    attribute_mapping: dict[str, str] = Field(default_factory=dict)
    ca_certificate: str | None = Field(default=None, max_length=20_000)

    _filter = field_validator("user_filter")(_user_filter)


SsoConnectionCreate = Annotated[
    OidcConnectionCreate | LdapConnectionCreate, Field(discriminator="protocol")
]


class SsoConnectionUpdate(BaseModel):
    """Changing anything the login depends on sends the connection back to ``draft``."""

    model_config = ConfigDict(extra="forbid")

    issuer: _Text | None = None
    client_id: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    client_secret: SecretStr | None = None
    scopes: list[Annotated[str, Field(min_length=1, max_length=100)]] | None = None
    claim_mapping: dict[str, str] | None = None
    server_url: _Text | None = None
    start_tls: bool | None = None
    bind_dn: _Text | None = None
    bind_password: SecretStr | None = None
    user_base_dn: _Text | None = None
    user_filter: _Text | None = None
    group_base_dn: str | None = None
    group_filter: str | None = None
    attribute_mapping: dict[str, str] | None = None
    ca_certificate: str | None = None
    jit_enabled: bool | None = None
    trust_email_claim: bool | None = None
    default_role: Role | None = None
    group_role_mapping: dict[str, JsonValue] | None = None
    disabled: bool | None = None

    @field_validator("issuer")
    @classmethod
    def _issuer(cls, value: str | None) -> str | None:
        return None if value is None else _https_issuer(value)

    @field_validator("user_filter")
    @classmethod
    def _filter(cls, value: str | None) -> str | None:
        return None if value is None else _user_filter(value)


class SsoConnectionRead(BaseModel):
    id: UUID
    protocol: Literal["oidc", "ldap"]
    status: Literal["draft", "tested", "active", "disabled"]
    issuer: str
    client_id: str | None
    has_secret: bool
    scopes: list[str]
    claim_mapping: dict[str, JsonValue]
    server_url: str | None
    start_tls: bool
    bind_dn: str | None
    user_base_dn: str | None
    user_filter: str | None
    group_base_dn: str | None
    group_filter: str | None
    attribute_mapping: dict[str, JsonValue]
    has_ca_certificate: bool
    group_role_mapping: dict[str, JsonValue]
    jit_enabled: bool
    trust_email_claim: bool
    default_role: Role
    tested_at: datetime | None
    redirect_uri: str | None
    created_at: datetime
    updated_at: datetime


class SsoConnectionList(BaseModel):
    items: list[SsoConnectionRead]


class SsoTestRequest(BaseModel):
    """LDAP tests take a directory account; OIDC tests take no body."""

    model_config = ConfigDict(extra="forbid")

    username: Annotated[str, Field(max_length=256)] | None = None
    password: SecretStr | None = None


class SsoTestResult(BaseModel):
    ok: bool
    error: str | None = None
    claims: dict[str, JsonValue] | None = None


class SsoTestResponse(BaseModel):
    authorize_url: str | None = None
    result: SsoTestResult | None = None
    connection: SsoConnectionRead
