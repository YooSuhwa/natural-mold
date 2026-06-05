from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

MEMORY_SCOPES = ("user", "agent")
MEMORY_ALLOWED_SCOPES = ("user", "agent", "both")
MEMORY_WRITE_POLICIES = ("off", "ask", "auto")
TRIGGER_MEMORY_WRITE_POLICIES = ("off", "auto")
AGENT_MEMORY_POLICY_OVERRIDES = ("inherit", "off", "ask", "auto")
AGENT_MEMORY_SCOPE_OVERRIDES = ("inherit", "agent_only", "user_and_agent")
AGENT_TRIGGER_MEMORY_POLICY_OVERRIDES = ("inherit", "off", "auto")
MEMORY_RECORD_STATUSES = ("active", "deleted")
MEMORY_PROPOSAL_STATUSES = ("pending", "approved", "rejected", "expired")


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
    created_at: Mapped[datetime] = mapped_column(default=_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=_now_naive,
        onupdate=_now_naive,
        nullable=False,
    )


class MemoryRecord(Base):
    __tablename__ = "memory_records"

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


class MemoryProposal(Base):
    __tablename__ = "memory_proposals"

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
