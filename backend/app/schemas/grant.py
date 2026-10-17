"""Share, access-list and ownership-transfer contracts (plan 9.7, 9.7.1, 10.1-10.2, 10.7)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

PrincipalType = Literal["user", "group", "organization"]
ShareLevel = Literal["consumer", "viewer", "editor", "manager"]
AccessLevel = Literal["consumer", "viewer", "editor", "manager", "owner", "governor"]
AccessSource = Literal["owner", "direct", "group", "organization", "governance"]
SharePermission = Literal["can_share", "can_revoke", "can_govern"]


class PrincipalRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: PrincipalType
    id: UUID


class PrincipalView(PrincipalRef):
    display_name: str | None = None
    member_count: int | None = None


class GrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal: PrincipalRef
    level: ShareLevel
    expires_at: datetime | None = None  # agents only (10.2)
    confirm_delegation: bool = False  # D17: delegated account resources acknowledged


class GrantView(BaseModel):
    grant_id: UUID
    principal: PrincipalRef
    level: ShareLevel
    expires_at: datetime | None
    granted_by: UUID | None
    granted_at: datetime


class GrantList(BaseModel):
    items: list[GrantView]


class AccessEntry(BaseModel):
    grant_id: UUID | None
    principal: PrincipalView
    level: AccessLevel
    source: AccessSource
    revocable: bool
    expires_at: datetime | None = None


class DelegatedAccount(BaseModel):
    """An account an agent lends to whoever runs it (D17; ``delegation_accounts``)."""

    resource_type: str
    resource_id: UUID
    name: str
    credential_id: UUID
    credential_name: str
    owner_name: str | None = None  # ``None``: a company credential, or an unknown owner


class AccessList(BaseModel):
    items: list[AccessEntry]
    my_permissions: list[SharePermission]
    delegations: list[DelegatedAccount] = []  # agents only: shown before sharing (E-06)


class OwnershipTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_owner_id: UUID
    reason: str | None = Field(default=None, max_length=500)
    keep_manager: bool = False  # leave the previous owner a manager grant (S17)


class OwnershipTransferResult(BaseModel):
    resource_type: str
    resource_id: UUID
    owner_id: UUID
    previous_owner_id: UUID | None
    manager_grant_id: UUID | None = None
