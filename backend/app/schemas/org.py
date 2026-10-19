"""Organization metadata and validated policy updates."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue, StringConstraints, field_validator

from app.authz.org_control import OrgControlPermission

RegistrationPolicy = Literal["default_org", "invite_only", "closed"]
DefaultMemberRole = Literal["member", "builder"]
OrganizationName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class OrganizationSettings(BaseModel):
    model_config = ConfigDict(frozen=True)
    default_member_role: DefaultMemberRole = "member"
    allow_public_share_links: bool = True
    registration_policy: RegistrationPolicy | None = None


class OrganizationSettingsPatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    default_member_role: DefaultMemberRole | None = None
    allow_public_share_links: bool | None = None
    registration_policy: RegistrationPolicy | None = None

    def update_values(self) -> dict[str, JsonValue]:
        values: dict[str, JsonValue] = {}
        if "default_member_role" in self.model_fields_set:
            values["default_member_role"] = self.default_member_role or "member"
        if "allow_public_share_links" in self.model_fields_set:
            values["allow_public_share_links"] = (
                self.allow_public_share_links if self.allow_public_share_links is not None else True
            )
        if "registration_policy" in self.model_fields_set:
            values["registration_policy"] = self.registration_policy
        return values


class OrganizationPatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: OrganizationName | None = None
    settings: OrganizationSettingsPatch | None = None

    @field_validator("name")
    @classmethod
    def explicit_name_is_text(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("name cannot be null")
        return value


class OrganizationView(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    tenant_id: UUID
    name: str
    slug: str
    status: Literal["active", "suspended", "archived"]
    is_default: bool
    settings: OrganizationSettings
    updated_at: datetime
    permissions: tuple[OrgControlPermission, ...]
