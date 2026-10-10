"""Tagged principals make role/capability target restrictions explicit."""

from datetime import datetime
from typing import Annotated, Literal, assert_never
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.organization import OrgCapability, OrgRole


class UserPrincipal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["user"]
    id: UUID


class GroupPrincipal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["group"]
    id: UUID


class OrganizationPrincipal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: Literal["organization"]
    id: UUID


RolePrincipal = Annotated[UserPrincipal | GroupPrincipal, Field(discriminator="type")]
Principal = Annotated[
    UserPrincipal | GroupPrincipal | OrganizationPrincipal, Field(discriminator="type")
]


class RoleAssignment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    principal: RolePrincipal
    roles: tuple[OrgRole, ...] = Field(max_length=3)

    @model_validator(mode="after")
    def unique_roles(self) -> "RoleAssignment":
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("roles must be unique")
        return self


class CapabilityAssignment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    principal: Principal
    capabilities: tuple[OrgCapability, ...] = Field(max_length=7)

    @model_validator(mode="after")
    def valid_targets(self) -> "CapabilityAssignment":
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("capabilities must be unique")
        match self.principal:
            case OrganizationPrincipal():
                if {"cap_connect_database", "cap_share_org_wide"}.intersection(self.capabilities):
                    raise ValueError("this capability requires a user or group principal")
            case UserPrincipal() | GroupPrincipal():
                pass
            case unknown:
                assert_never(unknown)
        return self


class RoleGrantView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    principal: RolePrincipal
    role: OrgRole
    granted_by: UUID | None
    granted_at: datetime


class CapabilityGrantView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    principal: Principal
    capability: OrgCapability
    granted_by: UUID | None
    granted_at: datetime
