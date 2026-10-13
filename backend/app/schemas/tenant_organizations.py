"""Company-governed organization summary models."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    StringConstraints,
    TypeAdapter,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.models.organization import Organization

OrganizationStatus = Literal["active", "suspended", "archived"]
ORGANIZATION_STATUS_ADAPTER = TypeAdapter(OrganizationStatus)
DefaultMemberRole = Literal["member", "builder"]
DEFAULT_MEMBER_ROLE_ADAPTER = TypeAdapter(DefaultMemberRole)
OrganizationName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class TenantOrganizationAdministrator(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: UUID | None = None
    email: EmailStr | None = None

    @model_validator(mode="after")
    def exactly_one_identity(self) -> "TenantOrganizationAdministrator":
        if (self.user_id is None) == (self.email is None):
            raise PydanticCustomError(
                "administrator_identity", "exactly one administrator identity is required"
            )
        return self


class TenantOrganizationCreate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: OrganizationName
    default_member_role: DefaultMemberRole = "member"
    administrator: TenantOrganizationAdministrator | None = None


class TenantOrganizationRename(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: OrganizationName


class TenantOrganizationAuditorSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    name: str
    member_count: int


@dataclass(frozen=True, slots=True)
class TenantOrganizationProjection:
    organization: Organization
    member_count: int
    administrator_user_id: UUID | None


class TenantOrganizationSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    name: str
    slug: str
    status: OrganizationStatus
    is_default: bool
    default_member_role: DefaultMemberRole
    member_count: int
    administrator_user_id: UUID | None
    updated_at: datetime

    @classmethod
    def from_projection(
        cls, projection: TenantOrganizationProjection
    ) -> "TenantOrganizationSummary":
        organization = projection.organization
        return cls(
            id=organization.id,
            name=organization.name,
            slug=organization.slug,
            status=ORGANIZATION_STATUS_ADAPTER.validate_python(organization.status),
            is_default=organization.is_default,
            default_member_role=DEFAULT_MEMBER_ROLE_ADAPTER.validate_python(
                organization.settings.get("default_member_role", "member")
            ),
            member_count=projection.member_count,
            administrator_user_id=projection.administrator_user_id,
            updated_at=organization.updated_at,
        )
