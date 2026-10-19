"""A-13: preferences, per-user credential connections, requests and notifications."""

from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from schema_drafts.org_authz.base import Created, DraftBase, Identity, OrgScope, Timestamps, org_fk


class AgentUserPreference(DraftBase):
    __tablename__ = "agent_user_preferences"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    is_favorite: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResourceCredentialBinding(Identity, OrgScope, Timestamps, DraftBase):
    __tablename__ = "resource_credential_bindings"
    __table_args__ = (
        org_fk(),
        CheckConstraint("resource_type IN ('tool','mcp_server')", name="resource_type"),
        UniqueConstraint("user_id", "resource_type", "resource_id"),
        Index("ix_resource_credential_bindings_resource", "org_id", "resource_type", "resource_id"),
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    resource_type: Mapped[str] = mapped_column(String(20))
    resource_id: Mapped[UUID] = mapped_column()
    credential_id: Mapped[UUID] = mapped_column(ForeignKey("credentials.id", ondelete="RESTRICT"))


class AccessRequest(Identity, OrgScope, Created, DraftBase):
    __tablename__ = "access_requests"
    __table_args__ = (
        org_fk(),
        CheckConstraint(
            "status IN ('pending','approved','denied','cancelled','expired')", name="status"
        ),
        CheckConstraint(
            "relation IN ('consumer','viewer','editor','manager','installer','explorer')",
            name="relation",
        ),
        Index(
            "uq_access_requests_pending",
            "resource_type",
            "resource_id",
            "requester_id",
            "relation",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
        Index("ix_access_requests_org_status", "org_id", "status", "created_at"),
    )
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[UUID] = mapped_column()
    relation: Mapped[str] = mapped_column(String(20))
    requester_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    decided_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)


class Notification(Identity, Created, DraftBase):
    __tablename__ = "notifications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["organizations.id", "organizations.tenant_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "kind IN ('access_request.created','access_request.decided',"
            "'grant.received','trigger.blocked','connection.owner_needed',"
            "'delegation.broken','ownership.transferred','org.invitation',"
            "'tenant.limit_warning','tenant.suspended','marketplace.publish_requested',"
            "'marketplace.publish_decided','usage.limit_warning','agent_memo.proposal')",
            name="kind",
        ),
        Index("ix_notifications_user_read_created", "user_id", "read_at", "created_at"),
    )
    org_id: Mapped[UUID | None] = mapped_column()
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, JsonValue]] = mapped_column(JSON, server_default="{}")
    link: Mapped[str | None] = mapped_column(String(1000))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
