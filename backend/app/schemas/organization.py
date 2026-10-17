"""Organization self-service request and response contracts."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

OrgRole = Literal["admin", "builder", "auditor"]
OrgCapability = Literal[
    "cap_create_agent",
    "cap_create_skill",
    "cap_create_tool",
    "cap_register_mcp",
    "cap_connect_database",
    "cap_share_org_wide",
    "cap_publish_marketplace",
]


class OrganizationSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str
    slug: str
    tenant_id: UUID
    tenant_name: str
    tenant_status: Literal["active", "suspended", "deletion_scheduled"]
    is_default: bool
    is_active: bool = False
    roles: tuple[OrgRole, ...] = ()
    capabilities: tuple[OrgCapability, ...] = ()
    # False while capabilities are not enforced (legacy/shadow): every member may create,
    # so screens gate create buttons on ``capabilities`` only when this is True (H-05).
    capabilities_enforced: bool = False


class ActiveOrganizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    org_id: UUID


class ActiveOrganizationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)
    org_id: UUID
    tenant_id: UUID
