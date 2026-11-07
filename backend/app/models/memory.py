from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.resource_scope import RequiredOrgScope, resource_scope_constraints

MEMORY_SCOPES = ("user", "agent")
MEMORY_ALLOWED_SCOPES = ("user", "agent", "both")
MEMORY_WRITE_POLICIES = ("off", "ask", "auto")
TRIGGER_MEMORY_WRITE_POLICIES = ("off", "auto")
AGENT_MEMORY_POLICY_OVERRIDES = ("inherit", "off", "ask", "auto")
AGENT_MEMORY_SCOPE_OVERRIDES = ("inherit", "agent_only", "user_and_agent")
AGENT_TRIGGER_MEMORY_POLICY_OVERRIDES = ("inherit", "off", "auto")
# Plan 15 (5.1). ``deleted`` is the legacy soft delete; a delete now removes the row.
MEMORY_RECORD_STATUSES = ("active", "superseded", "revoked", "deleted")
MEMORY_KINDS = ("fact", "preference", "commitment", "experience", "episode", "procedure")
MEMORY_SUBJECTS = ("self", "other")
# Strictest last (5.3-1): a derived memory takes the strictest origin of its sources.
MEMORY_ORIGINS = ("user_stated", "agent_inferred", "legacy", "consolidation", "tool_derived")
MEMORY_SENSITIVITIES = ("normal", "contact", "sensitive")
MEMORY_APPROVAL_CHANNELS = ("direct", "proposal", "auto_policy")
MEMORY_PROPOSAL_ACTIONS = ("create", "update", "supersede", "merge", "pin", "expire")
MEMORY_REVISION_ACTIONS = (
    "create",
    "update",
    "supersede",
    "revoke",
    "restore",
    "approve",
    "pin",
    "unpin",
)
# Plan 15 (5.11): the approval presets; ``custom`` reads the three axes.
MEMORY_APPROVAL_MODES = ("off", "read_only", "approve_all", "partial_auto", "auto_notify", "custom")
MEMORY_AXIS_VALUES = ("auto", "approve", "off")
MEMORY_PROPOSAL_STATUSES = ("pending", "approved", "rejected", "expired")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class UserMemorySettings(Base):
    __tablename__ = "user_memory_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    memory_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        default=True,
    )
    memory_read_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        default=True,
    )
    memory_write_policy: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="ask",
        default="ask",
    )
    allowed_scopes: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="both",
        default="both",
    )
    trigger_memory_write_policy: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="off",
        default="off",
    )
    # Plan 15 (5.11). ``None``: derived from ``memory_write_policy`` (migrated settings) and
    # the tenant default. ``approval_axes`` holds {"records","profile","consolidation"} for
    # ``custom``. ``consolidation_enabled``: the user's own switch for background tidying.
    approval_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    approval_axes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    consolidation_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    created_at: Mapped[datetime] = mapped_column(default=_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=_now_naive,
        onupdate=_now_naive,
        nullable=False,
    )


class AgentMemorySettings(Base):
    __tablename__ = "agent_memory_settings"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    memory_policy_override: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="inherit",
        default="inherit",
    )
    memory_scopes_override: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="inherit",
        default="inherit",
    )
    trigger_memory_policy_override: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="inherit",
        default="inherit",
    )
    # F-09 (11.8): the agent's shared operating memo is served to runs (enforce only).
    shared_memo_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    created_at: Mapped[datetime] = mapped_column(default=_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=_now_naive,
        onupdate=_now_naive,
        nullable=False,
    )


class MemoryRecord(RequiredOrgScope, Base):
    __tablename__ = "memory_records"
    __table_args__ = (
        *resource_scope_constraints("memory_records", system=False),
        CheckConstraint(_in("status", MEMORY_RECORD_STATUSES), name="ck_memory_records_status"),
        CheckConstraint(_in("kind", MEMORY_KINDS), name="ck_memory_records_kind"),
        CheckConstraint(_in("subject", MEMORY_SUBJECTS), name="ck_memory_records_subject"),
        CheckConstraint(_in("origin", MEMORY_ORIGINS), name="ck_memory_records_origin"),
        CheckConstraint(
            _in("sensitivity", MEMORY_SENSITIVITIES), name="ck_memory_records_sensitivity"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    store_path: Mapped[str] = mapped_column(String(200), nullable=False)
    source_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="active",
        default="active",
    )
    created_at: Mapped[datetime] = mapped_column(default=_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=_now_naive,
        onupdate=_now_naive,
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Plan 15 (5.1): what the memory is, where it came from and who approved it.
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="fact", default="fact"
    )
    subject: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="self", default="self"
    )
    subject_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    topic: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pinned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    # Existing rows are ``legacy``; every new write sets its origin explicitly (5.3-1).
    origin: Mapped[str] = mapped_column(String(20), nullable=False, server_default="legacy")
    sensitivity: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="normal", default="normal"
    )
    importance: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1", default=1)
    occurred_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    occurred_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    episode_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_records.id", ondelete="SET NULL"), nullable=True
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approval_channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_recalled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recall_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    flags: Mapped[dict] = mapped_column(
        JSON, nullable=False, server_default=text("'{}'"), default=dict
    )


class MemoryProposal(RequiredOrgScope, Base):
    __tablename__ = "memory_proposals"
    __table_args__ = (
        *resource_scope_constraints("memory_proposals", system=False),
        # F-09: ``agent_memo`` = a proposal to the agent's shared memo (11.8).
        CheckConstraint(
            "scope in ('user', 'agent', 'agent_memo')", name="ck_memory_proposals_scope"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="pending",
        default="pending",
    )
    created_at: Mapped[datetime] = mapped_column(default=_now_naive, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Plan 15 (5.1): the proposed memory's shape and what it would change.
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="fact", default="fact"
    )
    subject: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="self", default="self"
    )
    subject_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    topic: Mapped[str | None] = mapped_column(String(120), nullable=True)
    origin: Mapped[str] = mapped_column(String(20), nullable=False, server_default="legacy")
    sensitivity: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="normal", default="normal"
    )
    flags: Mapped[dict] = mapped_column(
        JSON, nullable=False, server_default=text("'{}'"), default=dict
    )
    occurred_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    occurred_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    action: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="create", default="create"
    )
    target_record_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_records.id", ondelete="SET NULL"), nullable=True
    )
    record_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_records.id", ondelete="SET NULL"), nullable=True
    )
    source_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)


class MemoryRevision(RequiredOrgScope, Base):
    """One change to a memory (5.7): who, on which path, before and after.

    Removed with the memory; revoke and delete blank the text columns.
    """

    __tablename__ = "memory_revisions"
    __table_args__ = (
        *resource_scope_constraints("memory_revisions", system=False),
        CheckConstraint(_in("action", MEMORY_REVISION_ACTIONS), name="ck_memory_revisions_action"),
        Index("ix_memory_revisions_record", "record_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memory_records.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    run_path: Mapped[str | None] = mapped_column(String(20), nullable=True)
    before_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now_naive, nullable=False)


class MemorySource(RequiredOrgScope, Base):
    """A derived memory's evidence (5.1): consolidation and profile lines cite these."""

    __tablename__ = "memory_sources"
    __table_args__ = (
        *resource_scope_constraints("memory_sources", system=False),
        PrimaryKeyConstraint("record_id", "source_record_id", name="pk_memory_sources"),
        ForeignKeyConstraint(
            ["record_id"],
            ["memory_records.id"],
            name="fk_memory_sources_record_id_memory_records",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_record_id"],
            ["memory_records.id"],
            name="fk_memory_sources_source_record_id_memory_records",
            ondelete="CASCADE",
        ),
        Index("ix_memory_sources_source", "source_record_id"),
    )

    record_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_record_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=_now_naive, nullable=False)


class MemoryErasure(RequiredOrgScope, Base):
    """A deleted memory's keyed digest (5.7): the same text cannot be written again.

    No text and no reversible hash: an HMAC with a per-company key (``memory_digest``).
    """

    __tablename__ = "memory_erasures"
    __table_args__ = (
        *resource_scope_constraints("memory_erasures", system=False),
        Index("ix_memory_erasures_user_digest", "user_id", "content_digest"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=_now_naive, nullable=False)
