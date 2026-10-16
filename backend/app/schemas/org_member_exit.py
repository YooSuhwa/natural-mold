"""Member-removal wizard contracts (plan 10.7 "구성원 제거", S15; E-02)."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ExitResourceType = Literal["agent", "skill", "tool", "mcp_server", "credential"]
ExitAction = Literal["transfer", "delete"]


class OwnedResource(BaseModel):
    resource_type: str
    resource_id: UUID
    name: str
    archived: bool = False  # agents in the trash stay with their owner (10.7 "보관")
    transferable: bool = True
    blocked_by: dict[str, object] | None = None  # 409 transfer_blocked details, if any


class HeldGrant(BaseModel):
    grant_id: UUID
    resource_type: str
    resource_id: UUID
    relation: str


class MemberExitPreview(BaseModel):
    """Step (1)-(2) of S15: what the removal transfers, revokes and stops."""

    user_id: UUID
    status: str
    is_last_admin: bool
    owned: list[OwnedResource]
    grants: list[HeldGrant]
    group_ids: list[UUID]
    roles: list[str]
    trigger_ids: list[UUID]  # active, paused on removal
    deployment_ids: list[UUID]  # active, disabled on removal
    api_key_ids: list[UUID]  # unrevoked, revoked on removal


class ExitResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_type: ExitResourceType
    resource_id: UUID
    action: ExitAction = "transfer"  # delete: credentials only; agents are archived first
    new_owner_id: UUID | None = None


class MemberRemoval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)  # governance audit reason (10.7)
    resolutions: list[ExitResolution] = Field(default_factory=list, max_length=500)


class MemberRemovalResult(BaseModel):
    user_id: UUID
    status: Literal["removed"]
    transferred: list[UUID]
    deleted: list[UUID]
    revoked_grant_ids: list[UUID]
