from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.conversation_run import utc_now_naive


class AgentPurgeReceipt(Base):
    """Durable idempotency authority for one physical Agent purge."""

    __tablename__ = "agent_purge_receipts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "org_id", "agent_id_snapshot", name="uq_agent_purge_receipts_agent"
        ),
        CheckConstraint(
            "state IN ('preparing', 'db_deleted', 'cleanup_pending', 'completed', 'failed')",
            name="ck_agent_purge_receipts_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    org_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    agent_id_snapshot: Mapped[uuid.UUID] = mapped_column(nullable=False)
    owner_user_id_snapshot: Mapped[uuid.UUID] = mapped_column(nullable=False)
    agent_name_snapshot: Mapped[str] = mapped_column(String(100), nullable=False)
    requester_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="preparing")
    failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utc_now_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)


class AgentPurgeCleanupItem(Base):
    """Retryable external cleanup item owned by a purge receipt."""

    __tablename__ = "agent_purge_cleanup_items"
    __table_args__ = (
        UniqueConstraint(
            "receipt_id", "kind", "locator", name="uq_agent_purge_cleanup_items_locator"
        ),
        CheckConstraint(
            "kind IN ('agent_image', 'agent_files', 'raw_checkpoint')",
            name="ck_agent_purge_cleanup_items_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')",
            name="ck_agent_purge_cleanup_items_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_agent_purge_cleanup_items_attempts"),
        CheckConstraint(
            "length(locator_sha256) = 64",
            name="ck_agent_purge_cleanup_items_locator_checksum",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_purge_receipts.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    locator: Mapped[str] = mapped_column(Text, nullable=False)
    locator_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utc_now_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)


__all__ = ["AgentPurgeCleanupItem", "AgentPurgeReceipt"]
