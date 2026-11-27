"""Tenant-scoped lifecycle and last recovered outputs of one goal workspace."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GoalSandbox(Base):
    __tablename__ = "goal_sandboxes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            name="fk_goal_sandboxes_org_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "goal_version > 0 AND policy_version > 0", name="ck_goal_sandboxes_versions"
        ),
        CheckConstraint(
            "state IN ('new','executing','ready','pausing','paused','needs_recovery',"
            "'recovering','retiring','deleting','deleted','unresolved')",
            name="ck_goal_sandboxes_state",
        ),
        CheckConstraint(
            "(state IN ('executing','pausing','recovering','deleting') AND "
            "operation_token IS NOT NULL AND operation_until IS NOT NULL) OR "
            "(state NOT IN ('executing','pausing','recovering','deleting') AND "
            "operation_token IS NULL AND operation_until IS NULL)",
            name="ck_goal_sandboxes_operation",
        ),
        Index(
            "uq_goal_sandboxes_live_scope",
            "scope_hash",
            unique=True,
            postgresql_where=text("state <> 'deleted'"),
            sqlite_where=text("state <> 'deleted'"),
        ),
        Index("ix_goal_sandboxes_maintenance", "tenant_id", "state", "updated_at"),
        Index(
            "ix_goal_sandboxes_conversation", "tenant_id", "org_id", "user_id", "conversation_id"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID]
    org_id: Mapped[UUID]
    # Identity snapshots let provider cleanup complete after the source goal or user is erased.
    user_id: Mapped[UUID]
    conversation_id: Mapped[UUID]
    goal_id: Mapped[UUID]
    goal_version: Mapped[int]
    agent_id: Mapped[UUID]
    policy_version: Mapped[int]
    policy_hash: Mapped[str] = mapped_column(String(64))
    image: Mapped[str] = mapped_column(String(512))
    skill_id: Mapped[UUID]
    skill_hash: Mapped[str] = mapped_column(String(64))
    skill_set: Mapped[dict[str, str]] = mapped_column(JSON)
    scope_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20))
    provider_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    operation_token: Mapped[UUID | None]
    operation_until: Mapped[datetime | None] = mapped_column(DateTime())
    output_run_id: Mapped[UUID | None]
    pending_run_id: Mapped[UUID | None]
    receipt: Mapped[dict[str, str]] = mapped_column(JSON)
    pending_manifest: Mapped[dict[str, str] | None] = mapped_column(JSON(none_as_null=True))
    resolution_reason: Mapped[str | None] = mapped_column(String(32))
    last_used_at: Mapped[datetime] = mapped_column(DateTime())
    updated_at: Mapped[datetime] = mapped_column(DateTime())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime())
    retry_after: Mapped[datetime | None] = mapped_column(DateTime())


class GoalSandboxRead(Base):
    __tablename__ = "goal_sandbox_reads"
    __table_args__ = (Index("ix_goal_sandbox_reads_expiry", "workspace_id", "expires_at"),)
    token: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("goal_sandboxes.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime())
