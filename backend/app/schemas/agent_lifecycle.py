"""Frozen request and response contracts for Agent lifecycle governance."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

AgentLifecycleState = Literal["active", "archived_stopping", "archived_stopped", "purged"]
AgentPurgeReceiptState = Literal[
    "preparing", "db_deleted", "cleanup_pending", "completed", "failed"
]


class AgentLifecycleMetadataResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)

    agent_id: UUID
    name: str
    owner_user_id: UUID
    owner_label: str
    archived_by_user_id: UUID | None
    archived_at: datetime | None
    lifecycle_state: AgentLifecycleState
    policy_delete_at: datetime | None
    can_archive: bool
    can_restore: bool
    can_purge: bool


class AgentRestoreResponse(AgentLifecycleMetadataResponse):
    """Restore returns the same metadata-only governance projection."""


class AgentPurgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    confirmation_name: str = Field(min_length=1, max_length=100)


class AgentPurgeReceiptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_id: UUID
    agent_id: UUID
    state: AgentPurgeReceiptState
    created_at: datetime
    updated_at: datetime
