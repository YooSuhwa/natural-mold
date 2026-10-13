"""Company member and role response models."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, TypeAdapter, model_validator
from pydantic_core import PydanticCustomError

from app.models.organization import TenantMember
from app.models.user import User
from app.tenancy.context import TenantRole

TenantMemberStatus = Literal["active", "suspended", "removed"]
TENANT_MEMBER_STATUS_ADAPTER = TypeAdapter(TenantMemberStatus)


class TenantMemberAdd(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: UUID


class TenantMemberStatusPatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["active", "suspended"]


class TenantRoleReplacement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    roles: tuple[TenantRole, ...]

    @model_validator(mode="after")
    def unique_roles(self) -> "TenantRoleReplacement":
        if len(set(self.roles)) != len(self.roles):
            raise PydanticCustomError("tenant_roles", "tenant roles must be unique")
        return self


@dataclass(frozen=True, slots=True)
class TenantMemberProjection:
    member: TenantMember
    user: User
    roles: tuple[TenantRole, ...]


class TenantMemberSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: UUID
    display_name: str
    status: TenantMemberStatus
    roles: tuple[TenantRole, ...]
    created_at: datetime

    @classmethod
    def from_projection(cls, projection: TenantMemberProjection) -> "TenantMemberSummary":
        return cls(
            user_id=projection.member.user_id,
            display_name=projection.user.display_name or projection.user.name,
            status=TENANT_MEMBER_STATUS_ADAPTER.validate_python(projection.member.status),
            roles=projection.roles,
            created_at=projection.member.created_at,
        )


class TenantMemberAdminView(TenantMemberSummary):
    email: EmailStr

    @classmethod
    def from_projection(cls, projection: TenantMemberProjection) -> "TenantMemberAdminView":
        return cls(
            user_id=projection.member.user_id,
            display_name=projection.user.display_name or projection.user.name,
            email=projection.user.email,
            status=TENANT_MEMBER_STATUS_ADAPTER.validate_python(projection.member.status),
            roles=projection.roles,
            created_at=projection.member.created_at,
        )
