from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MemoryScope = Literal["user", "agent"]
AllowedMemoryScopes = Literal["user", "agent", "both"]
MemoryWritePolicy = Literal["off", "ask", "auto"]
TriggerMemoryWritePolicy = Literal["off", "auto"]
AgentMemoryPolicyOverride = Literal["inherit", "off", "ask", "auto"]
AgentMemoryScopesOverride = Literal["inherit", "agent_only", "user_and_agent"]
AgentTriggerMemoryPolicyOverride = Literal["inherit", "off", "auto"]
MemoryRecordStatus = Literal["active", "deleted"]
MemoryProposalStatus = Literal["pending", "approved", "rejected", "expired"]

MAX_MEMORY_CONTENT_LENGTH = 4000
MAX_MEMORY_REASON_LENGTH = 1000


def _strip_non_empty(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be empty")
    return stripped


def _reject_explicit_nulls(data: Any, fields: tuple[str, ...]) -> Any:
    if isinstance(data, dict):
        for field in fields:
            if field in data and data[field] is None:
                raise ValueError(f"{field} must not be null")
    return data


class UserMemorySettingsOut(BaseModel):
    memory_enabled: bool
    memory_read_enabled: bool
    memory_write_policy: MemoryWritePolicy
    allowed_scopes: AllowedMemoryScopes
    trigger_memory_write_policy: TriggerMemoryWritePolicy

    model_config = ConfigDict(from_attributes=True)


class UserMemorySettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_enabled: bool | None = None
    memory_read_enabled: bool | None = None
    memory_write_policy: MemoryWritePolicy | None = None
    allowed_scopes: AllowedMemoryScopes | None = None
    trigger_memory_write_policy: TriggerMemoryWritePolicy | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_null_updates(cls, data: Any) -> Any:
        return _reject_explicit_nulls(
            data,
            (
                "memory_enabled",
                "memory_read_enabled",
                "memory_write_policy",
                "allowed_scopes",
                "trigger_memory_write_policy",
            ),
        )


class AgentMemorySettingsOut(BaseModel):
    memory_policy_override: AgentMemoryPolicyOverride
    memory_scopes_override: AgentMemoryScopesOverride
    trigger_memory_policy_override: AgentTriggerMemoryPolicyOverride

    model_config = ConfigDict(from_attributes=True)


class AgentMemorySettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_policy_override: AgentMemoryPolicyOverride | None = None
    memory_scopes_override: AgentMemoryScopesOverride | None = None
    trigger_memory_policy_override: AgentTriggerMemoryPolicyOverride | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_null_updates(cls, data: Any) -> Any:
        return _reject_explicit_nulls(
            data,
            (
                "memory_policy_override",
                "memory_scopes_override",
                "trigger_memory_policy_override",
            ),
        )


class MemoryRecordCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: MemoryScope
    content: str = Field(min_length=1, max_length=MAX_MEMORY_CONTENT_LENGTH)
    reason: str | None = Field(default=None, max_length=MAX_MEMORY_REASON_LENGTH)
    agent_id: uuid.UUID | None = None
    source_conversation_id: uuid.UUID | None = None
    source_message_id: str | None = Field(default=None, max_length=128)
    source_run_id: str | None = Field(default=None, max_length=128)

    @field_validator("content")
    @classmethod
    def _clean_content(cls, value: str) -> str:
        return _strip_non_empty(value)

    @field_validator("reason")
    @classmethod
    def _clean_reason(cls, value: str | None) -> str | None:
        return _strip_non_empty(value) if value is not None else None


class MemoryRecordUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = Field(default=None, min_length=1, max_length=MAX_MEMORY_CONTENT_LENGTH)
    reason: str | None = Field(default=None, max_length=MAX_MEMORY_REASON_LENGTH)

    @model_validator(mode="before")
    @classmethod
    def _reject_null_updates(cls, data: Any) -> Any:
        return _reject_explicit_nulls(data, ("content",))

    @field_validator("content")
    @classmethod
    def _clean_content(cls, value: str | None) -> str | None:
        return _strip_non_empty(value) if value is not None else None

    @field_validator("reason")
    @classmethod
    def _clean_reason(cls, value: str | None) -> str | None:
        return _strip_non_empty(value) if value is not None else None


class MemoryRecordOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    agent_id: uuid.UUID | None = None
    scope: MemoryScope
    content: str
    reason: str | None = None
    store_path: str
    source_conversation_id: uuid.UUID | None = None
    source_message_id: str | None = None
    source_run_id: str | None = None
    status: MemoryRecordStatus
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class MemoryProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: MemoryScope
    content: str = Field(min_length=1, max_length=MAX_MEMORY_CONTENT_LENGTH)
    reason: str | None = Field(default=None, max_length=MAX_MEMORY_REASON_LENGTH)
    agent_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    source_run_id: str | None = Field(default=None, max_length=128)

    @field_validator("content")
    @classmethod
    def _clean_content(cls, value: str) -> str:
        return _strip_non_empty(value)

    @field_validator("reason")
    @classmethod
    def _clean_reason(cls, value: str | None) -> str | None:
        return _strip_non_empty(value) if value is not None else None


class MemoryProposalOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    agent_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    source_run_id: str | None = None
    scope: MemoryScope
    content: str
    reason: str | None = None
    status: MemoryProposalStatus
    created_at: datetime
    resolved_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class MemoryProposalEditApprove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=MAX_MEMORY_CONTENT_LENGTH)
    reason: str | None = Field(default=None, max_length=MAX_MEMORY_REASON_LENGTH)

    @field_validator("content")
    @classmethod
    def _clean_content(cls, value: str) -> str:
        return _strip_non_empty(value)

    @field_validator("reason")
    @classmethod
    def _clean_reason(cls, value: str | None) -> str | None:
        return _strip_non_empty(value) if value is not None else None


class MemoryProposalApprovalOut(BaseModel):
    proposal: MemoryProposalOut
    memory: MemoryRecordOut


class EffectiveMemoryPolicyOut(BaseModel):
    read_enabled: bool
    write_policy: MemoryWritePolicy
    allowed_scopes: AllowedMemoryScopes
    trigger_write_policy: TriggerMemoryWritePolicy
