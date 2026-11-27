from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.resource_scope import NullableOrgScope, RequiredOrgScope, resource_scope_constraints

if TYPE_CHECKING:
    from app.models.conversation_run_metrics import ConversationRunMetrics

RUN_ACTIVE_STATUSES = ("queued", "running", "canceling")
RUN_TERMINAL_STATUSES = ("completed", "failed", "interrupted", "canceled", "stale")
RUN_STATUS_VALUES = RUN_ACTIVE_STATUSES + RUN_TERMINAL_STATUSES
RUN_EVENT_ID_MAX_LENGTH = 255

_RUNTIME_MATERIALIZATION_HEX_CHECK = (
    "runtime_materialization_sha256 IS NULL OR ("
    "length(runtime_materialization_sha256) = 64 AND "
    "replace(replace(replace(replace(replace(replace(replace(replace("
    "replace(replace(replace(replace(replace(replace(replace(replace("
    "runtime_materialization_sha256,'0',''),'1',''),'2',''),'3',''),'4',''),'5',''),"
    "'6',''),'7',''),'8',''),'9',''),'a',''),'b',''),'c',''),'d',''),'e',''),'f','') = '')"
)


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ConversationRun(NullableOrgScope, Base):
    __tablename__ = "conversation_runs"
    __table_args__ = (
        *resource_scope_constraints("conversation_runs", system=False),
        Index("uq_conversation_runs_id_scope", "id", "tenant_id", "org_id", unique=True),
        Index("ix_conversation_runs_org_created", "org_id", "created_at"),
        Index(
            "ix_conversation_runs_conversation_created",
            "conversation_id",
            "created_at",
        ),
        Index("ix_conversation_runs_agent_created", "agent_id", "created_at"),
        Index("ix_conversation_runs_user_status", "user_id", "status"),
        Index("ix_conversation_runs_status_heartbeat", "status", "heartbeat_at"),
        Index(
            "uq_conversation_runs_active_conversation",
            "conversation_id",
            unique=True,
            sqlite_where=text("is_active = 1"),
            postgresql_where=text("is_active = true"),
        ),
        CheckConstraint(
            "(runtime_materialized_at IS NULL AND runtime_materialization_sha256 IS NULL) OR "
            "(runtime_materialized_at IS NOT NULL "
            "AND runtime_materialization_sha256 IS NOT NULL)",
            name="ck_conversation_runs_runtime_materialization_pair",
        ),
        CheckConstraint(
            _RUNTIME_MATERIALIZATION_HEX_CHECK,
            name="ck_conversation_runs_runtime_materialization_sha256",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversation_runs.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    worker_instance_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    owner_generation: Mapped[int] = mapped_column(
        Integer,
        CheckConstraint("owner_generation >= 0", name="ck_run_owner_generation"),
        default=0,
        server_default="0",
        nullable=False,
    )
    owner_token: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    recovery_attempts: Mapped[int] = mapped_column(
        Integer,
        CheckConstraint("recovery_attempts >= 0", name="ck_run_recovery_attempts"),
        default=0,
        server_default="0",
        nullable=False,
    )
    recovery_runtime_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recovery_model_attempts: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    interrupt_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input_preview: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_event_id: Mapped[str | None] = mapped_column(
        String(RUN_EVENT_ID_MAX_LENGTH), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    cancel_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cancellation_acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utc_now_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default=utc_now_naive,
        onupdate=utc_now_naive,
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # F-14 (11.10): user_id is the requester/billed user; these record the rest.
    run_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    invoker_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    resource_owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    on_behalf_of_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    credential_subject_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    runtime_policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runtime_policy_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runtime_policy_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    runtime_materialized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    runtime_materialization_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metrics: Mapped[ConversationRunMetrics | None] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="selectin",
        uselist=False,
    )


class ConversationRunToolReceipt(RequiredOrgScope, Base):
    """Durable execution evidence scoped to a specific tool invocation and chat run."""

    __tablename__ = "conversation_run_tool_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "tenant_id", "org_id"],
            ["conversation_runs.id", "conversation_runs.tenant_id", "conversation_runs.org_id"],
            name="fk_run_tool_receipt_scoped_run",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "effect_kind IN ('read','idempotent_write','external_write')",
            name="ck_run_tool_receipt_effect",
        ),
        CheckConstraint("status IN ('started','completed')", name="ck_run_tool_receipt_status"),
        CheckConstraint("owner_generation > 0", name="ck_run_tool_receipt_generation"),
        CheckConstraint("length(arguments_sha256) = 64", name="ck_run_tool_receipt_digest"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tool_call_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    arguments_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    effect_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    owner_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# F-14: columns audit writers read back from ``load_only`` run rows (``tenancy.attribution``).
RUN_ATTRIBUTION_COLUMNS = (
    "org_id",
    "tenant_id",
    "run_source",
    "invoker_type",
    "resource_owner_user_id",
    "on_behalf_of_user_id",
    "credential_subject_user_id",
)


def run_attribution_attrs() -> tuple[Any, ...]:
    """The attribution attributes, for ``load_only(...)`` lists."""
    return tuple(getattr(ConversationRun, name) for name in RUN_ATTRIBUTION_COLUMNS)
