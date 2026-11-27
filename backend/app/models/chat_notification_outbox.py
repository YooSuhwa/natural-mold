"""A durable delivery receipt for each goal transition or completed independent job."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.organization_base import Created, Identity, org_fk
from app.models.resource_scope import RequiredOrgScope

_TABLE = "chat_notification_outbox"


class ChatNotificationOutbox(Identity, Created, RequiredOrgScope, Base):
    __tablename__ = _TABLE
    __table_args__ = (
        org_fk(_TABLE, "CASCADE"),
        UniqueConstraint("user_id", "kind", "source_id", name="uq_chat_notice_recipient_source"),
        CheckConstraint(
            "(kind = 'chat_task.completed' AND goal_id IS NULL AND goal_version IS NULL "
            "AND goal_revision IS NULL) OR (kind IN ('chat_goal.completed','chat_goal.blocked') "
            "AND goal_id IS NOT NULL AND goal_version IS NOT NULL AND goal_revision IS NOT NULL "
            "AND goal_version > 0 AND goal_revision > 0)",
            name="ck_chat_notice_source",
        ),
        CheckConstraint(
            "(artifact_id IS NULL) = (version_id IS NULL)", name="ck_chat_notice_artifact_version"
        ),
        CheckConstraint(
            "(status = 'pending' AND finished_at IS NULL AND notification_id IS NULL) OR "
            "(status IN ('delivered','suppressed') AND finished_at IS NOT NULL)",
            name="ck_chat_notice_delivery",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_chat_notice_attempts"),
        Index(
            "ix_chat_notice_pending_tenants",
            "available_at",
            "tenant_id",
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
        Index("ix_chat_notice_due", "tenant_id", "status", "available_at", "id"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    run_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_runs.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(48))
    source_id: Mapped[UUID]
    goal_id: Mapped[UUID | None]
    goal_version: Mapped[int | None]
    goal_revision: Mapped[int | None]
    artifact_id: Mapped[UUID | None]
    version_id: Mapped[UUID | None]
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    attempt_count: Mapped[int] = mapped_column(server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notification_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("notifications.id", ondelete="SET NULL")
    )
