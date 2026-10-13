"""Opaque, scope-bound keyset cursors for company administration lists."""

import base64
from datetime import datetime
from typing import Literal, Self, assert_never
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.authz.error_codes import authorization_error

TenantListKind = Literal[
    "tenant_organizations",
    "tenant_members",
    "tenant_roles",
    "tenant_invitations",
    "platform_tenants",
]


class TenantCursorScope(BaseModel):
    """The tenant and filter identity a cursor is authorized to resume."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    list_kind: TenantListKind
    tenant_id: UUID | None
    filter_key: str = Field(min_length=0, max_length=1024)

    @model_validator(mode="after")
    def valid_scope(self) -> Self:
        match self.list_kind:
            case "platform_tenants":
                if self.tenant_id is not None:
                    raise ValueError("platform list cannot have a tenant scope")
            case "tenant_organizations" | "tenant_members" | "tenant_roles" | "tenant_invitations":
                if self.tenant_id is None:
                    raise ValueError("tenant list requires a tenant scope")
            case unreachable:
                assert_never(unreachable)
        return self


class TenantCursor(BaseModel):
    """A stable sort position that can only continue one list scope."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: TenantCursorScope
    sort_value: datetime
    position: UUID


class TenantPageQuery(BaseModel):
    """Common company-list input limits for future HTTP query models."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cursor: str | None = Field(default=None, max_length=2048)
    limit: int = Field(default=50, ge=1, le=200)


def encode_cursor(cursor: TenantCursor) -> str:
    """Serialize a company-list position without adding a signed cursor scheme."""

    raw = cursor.model_dump_json().encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(raw: str | None, scope: TenantCursorScope) -> TenantCursor | None:
    """Return a cursor only when its serialized list scope exactly matches the request."""

    if raw is None:
        return None
    try:
        cursor = TenantCursor.model_validate_json(
            base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
        )
    except ValueError as error:
        raise authorization_error("invalid_cursor") from error
    if cursor.scope != scope:
        raise authorization_error("invalid_cursor")
    return cursor
