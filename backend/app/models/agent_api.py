from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class AgentDeployment(Base):
    __tablename__ = "agent_deployments"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_agent_deployments_public_id"),
        UniqueConstraint("agent_id", name="uq_agent_deployments_agent_id"),
        Index("ix_agent_deployments_user_id", "user_id"),
        Index("ix_agent_deployments_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    public_id: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    allow_streaming: Mapped[bool] = mapped_column(default=True, nullable=False)
    allow_background: Mapped[bool] = mapped_column(default=False, nullable=False)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(nullable=True)
    daily_token_limit: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )

    agent = relationship("Agent", lazy="selectin")
    api_key_links: Mapped[list[AgentApiKeyDeployment]] = relationship(
        back_populates="deployment", cascade="all, delete-orphan"
    )


class AgentApiKey(Base):
    __tablename__ = "agent_api_keys"
    __table_args__ = (
        UniqueConstraint("key_id", name="uq_agent_api_keys_key_id"),
        Index("ix_agent_api_keys_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_id: Mapped[str] = mapped_column(String(40), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prefix: Mapped[str] = mapped_column(String(80), nullable=False)
    last_four: Mapped[str] = mapped_column(String(4), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    allow_all_deployments: Mapped[bool] = mapped_column(default=False, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    usage_count: Mapped[int] = mapped_column(default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )

    deployment_links: Mapped[list[AgentApiKeyDeployment]] = relationship(
        back_populates="api_key", cascade="all, delete-orphan", lazy="selectin"
    )


class AgentApiKeyDeployment(Base):
    __tablename__ = "agent_api_key_deployments"
    __table_args__ = (
        UniqueConstraint("api_key_id", "deployment_id", name="uq_agent_api_key_deployment"),
        Index("ix_agent_api_key_deployments_deployment_id", "deployment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_api_keys.id", ondelete="CASCADE"), nullable=False
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive, nullable=False)

    api_key: Mapped[AgentApiKey] = relationship(back_populates="deployment_links")
    deployment: Mapped[AgentDeployment] = relationship(
        back_populates="api_key_links", lazy="selectin"
    )


class AgentApiThread(Base):
    __tablename__ = "agent_api_threads"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_agent_api_threads_public_id"),
        Index("ix_agent_api_threads_user_id", "user_id"),
        Index("ix_agent_api_threads_deployment_id", "deployment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    public_id: Mapped[str] = mapped_column(String(80), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    external_user: Mapped[str | None] = mapped_column(String(200), nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )

    deployment = relationship("AgentDeployment", lazy="selectin")
    conversation = relationship("Conversation", lazy="selectin")


class AgentApiRun(Base):
    __tablename__ = "agent_api_runs"
    __table_args__ = (
        Index("ix_agent_api_runs_user_id", "user_id"),
        Index("ix_agent_api_runs_deployment_id", "deployment_id"),
        Index("ix_agent_api_runs_thread_id", "thread_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    public_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_api_keys.id", ondelete="SET NULL"), nullable=True
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_api_threads.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    input: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    deployment = relationship("AgentDeployment", lazy="selectin")
    thread = relationship("AgentApiThread", lazy="selectin")
