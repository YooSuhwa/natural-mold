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
MemoryRecordStatus = Literal["active", "superseded", "revoked", "deleted"]
MemoryKind = Literal["fact", "preference", "commitment", "experience", "episode", "procedure"]
MemoryOrigin = Literal["user_stated", "agent_inferred", "legacy", "consolidation", "tool_derived"]
MemorySensitivity = Literal["normal", "contact", "sensitive"]
ApprovalMode = Literal["off", "read_only", "approve_all", "partial_auto", "auto_notify", "custom"]
ApprovalAxis = Literal["auto", "approve", "off"]
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


class ApprovalAxes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: ApprovalAxis = "approve"
    profile: ApprovalAxis = "approve"
    consolidation: ApprovalAxis = "approve"


class UserMemorySettingsOut(BaseModel):
    memory_enabled: bool
    memory_read_enabled: bool
    memory_write_policy: MemoryWritePolicy
    allowed_scopes: AllowedMemoryScopes
    trigger_memory_write_policy: TriggerMemoryWritePolicy
    approval_mode: ApprovalMode | None = None
    approval_axes: ApprovalAxes | None = None
    consolidation_enabled: bool = True

    model_config = ConfigDict(from_attributes=True)


class UserMemorySettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_enabled: bool | None = None
    memory_read_enabled: bool | None = None
    memory_write_policy: MemoryWritePolicy | None = None
    allowed_scopes: AllowedMemoryScopes | None = None
    trigger_memory_write_policy: TriggerMemoryWritePolicy | None = None
    # Plan 15 (5.11): the approval preset; ``custom`` also takes ``approval_axes``.
    approval_mode: ApprovalMode | None = None
    approval_axes: ApprovalAxes | None = None
    consolidation_enabled: bool | None = None

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
                "approval_mode",
                "consolidation_enabled",
            ),
        )


class MemoryPolicyOut(BaseModel):
    """The approval mode in force and what the tenant allows (plan 15, 5.11, Q9)."""

    tenant_kind: Literal["company", "personal"]
    effective_mode: ApprovalMode
    axes: ApprovalAxes
    allowed_modes: list[ApprovalMode]
    default_mode: ApprovalMode
    consolidation_enabled: bool  # the tenant's switch and the person's both on
    tenant_consolidation_enabled: bool


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
    kind: MemoryKind = "fact"
    subject: Literal["self", "other"] = "self"
    subject_label: str | None = None
    topic: str | None = None
    title: str | None = None
    pinned: bool = False
    origin: MemoryOrigin = "legacy"
    sensitivity: MemorySensitivity = "normal"
    importance: int = 1
    occurred_start: datetime | None = None
    occurred_end: datetime | None = None
    valid_until: datetime | None = None
    superseded_by_id: uuid.UUID | None = None
    approved_by: uuid.UUID | None = None
    approved_at: datetime | None = None
    approval_channel: str | None = None
    revoked_at: datetime | None = None
    revoke_reason: str | None = None
    recall_count: int = 0
    flags: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class MemoryRevisionOut(BaseModel):
    id: uuid.UUID
    action: str
    actor_user_id: uuid.UUID | None = None
    run_path: str | None = None
    before_content: str | None = None
    after_content: str | None = None
    reason: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoryRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=200)


class MemoryBulkDelete(BaseModel):
    """Plan 15 (5.7): delete every memory in a scope; ``confirm`` must equal the count shown."""

    model_config = ConfigDict(extra="forbid")

    scope: Literal["all", "user", "agent"]
    agent_id: uuid.UUID | None = None
    confirm_count: int = Field(ge=0)


class MemoryBulkDeleteOut(BaseModel):
    deleted: int
    proposals_cleared: int


class MemoryDeletionReceipt(BaseModel):
    """What a delete erased and what it cannot reach (plan 15, 5.7 삭제 영수증)."""

    deleted: int
    revisions_removed: int
    proposals_cleared: int
    derived_marked: int
    kept_elsewhere: list[str]


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


class MemoryProposalSourceOut(BaseModel):
    id: uuid.UUID
    content: str
    status: str
    kind: str
    provenance: str


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
    kind: MemoryKind = "fact"
    subject: Literal["self", "other"] = "self"
    topic: str | None = None
    origin: MemoryOrigin = "legacy"
    sensitivity: MemorySensitivity = "normal"
    flags: dict[str, Any] = Field(default_factory=dict)
    action: str = "create"
    record_id: uuid.UUID | None = None
    # Plan 15 (5.6): what a consolidation proposal changes, for the before/after view.
    target_record_id: uuid.UUID | None = None
    source_ids: list[str] | None = None
    profile_change: dict[str, Any] | None = None
    sources: list[MemoryProposalSourceOut] = Field(default_factory=list)
    # Why it cannot be approved in bulk (None: it can).
    bulk_block: str | None = None

    model_config = ConfigDict(from_attributes=True)


class MemoryBulkApprove(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class MemoryBulkSkip(BaseModel):
    id: uuid.UUID
    reason: str


class MemoryBulkApproveOut(BaseModel):
    approved: list[uuid.UUID]
    skipped: list[MemoryBulkSkip]


class MemoryTenantPolicyValues(BaseModel):
    default_mode: ApprovalMode
    allowed_modes: list[ApprovalMode] = Field(min_length=1)
    consolidation_enabled: bool
    channel_personal_memory: bool
    contacts_allowed: bool


class MemoryTenantPolicyOut(BaseModel):
    tenant_kind: Literal["company", "personal"]
    values: MemoryTenantPolicyValues
    defaults: MemoryTenantPolicyValues
    can_manage: bool


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
    # None for a profile line change (no memory row is made or changed).
    memory: MemoryRecordOut | None = None


class EffectiveMemoryPolicyOut(BaseModel):
    read_enabled: bool
    write_policy: MemoryWritePolicy
    allowed_scopes: AllowedMemoryScopes
    trigger_write_policy: TriggerMemoryWritePolicy


class MemoryActivityOut(BaseModel):
    """One of the person's own memory audit events (plan 15, 5.9 활동 tab); no text."""

    id: uuid.UUID
    action: str
    target_type: str
    target_id: str | None = None
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


# Plan 15 (PR-B2): profile documents (5.12) and the recall preview (5.9).


class MemoryProfileLineIn(BaseModel):
    """``id`` keeps an existing line (and its sources) when its text is unchanged."""

    id: str | None = Field(default=None, max_length=64)
    text: str = Field(min_length=1, max_length=300)


class MemoryProfileUpdate(BaseModel):
    agent_id: uuid.UUID | None = None
    lines: list[MemoryProfileLineIn] = Field(default_factory=list, max_length=60)
    # The version the person edited; a newer one on the server refuses the write.
    version: int | None = Field(default=None, ge=0)


class MemoryProfileLineOut(BaseModel):
    id: str
    text: str
    origin: Literal["user", "pinned", "consolidation"]
    sources: list[str] = Field(default_factory=list)
    # False: a source was revoked or deleted, so runs skip the line.
    readable: bool


class MemoryProfileOut(BaseModel):
    kind: Literal["user_profile", "work_memo"]
    agent_id: uuid.UUID | None = None
    lines: list[MemoryProfileLineOut]
    tokens: int
    budget_tokens: int
    version: int


class MemoryProfilesOut(BaseModel):
    user_profile: MemoryProfileOut
    work_memo: MemoryProfileOut | None = None


class MemoryRecallPreviewIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    agent_id: uuid.UUID | None = None
    # Earlier user messages of the imagined conversation, latest first (at most two).
    previous: list[str] = Field(default_factory=list, max_length=2)


class MemoryRecallPreviewItem(BaseModel):
    id: uuid.UUID
    scope: str
    kind: str
    content: str
    keyword: float
    cosine: float | None = None
    search: float
    final: float
    included: bool
    # Why a passing memory stays out: the token budget or its scope's ceiling.
    left_out: Literal["budget", "scope_cap"] | None = None


class MemoryRecallPreviewOut(BaseModel):
    mode: Literal["fresh", "skipped", "reference"]
    analyzer: str
    # on: vectors used; unmeasured: a model without a measured floor; off: no model.
    vector: Literal["on", "unmeasured", "off"]
    profile_lines: list[str] = Field(default_factory=list)
    items: list[MemoryRecallPreviewItem] = Field(default_factory=list)
    tokens: int = 0
    budget_tokens: int


# Plan 15 Q14: the company's memory vector model.


class MemoryVectorModelOut(BaseModel):
    id: uuid.UUID
    display_name: str
    model_name: str
    provider: str
    dimensions: int
    source: Literal["platform", "company"]


class MemoryVectorStatusOut(BaseModel):
    model: MemoryVectorModelOut | None = None
    candidates: list[MemoryVectorModelOut] = Field(default_factory=list)
    total: int
    embedded: int
    other_vectors: int
    job_state: str | None = None
    job_progress: int | None = None
    # False: the chosen model has no measured floor, so recall still uses keywords only.
    floor_measured: bool
    can_manage: bool


class MemoryVectorUpdate(BaseModel):
    model_id: uuid.UUID | None = None
    # The number of memories the screen said would be embedded again.
    confirm_count: int = Field(ge=0)
    # Turning off: also remove the stored vectors (kept by default).
    drop_vectors: bool = False
