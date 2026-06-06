from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base

ARTIFACT_STATUS_VALUES = ("writing", "ready", "deleted", "failed")
ARTIFACT_STORAGE_VALUES = ("local", "s3")
ARTIFACT_KIND_VALUES = (
    "image",
    "video",
    "audio",
    "pdf",
    "markdown",
    "html",
    "code",
    "document",
    "data",
    "cad",
    "other",
)


class ConversationArtifact(Base):
    __tablename__ = "conversation_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "assistant_msg_id",
            "logical_path",
            name="uq_conversation_artifacts_turn_path",
        ),
        Index(
            "ix_conversation_artifacts_user_conversation_created",
            "user_id",
            "conversation_id",
            "created_at",
        ),
        Index(
            "ix_conversation_artifacts_conversation_turn_updated",
            "conversation_id",
            "assistant_msg_id",
            "updated_at",
        ),
        Index("ix_conversation_artifacts_user_created", "user_id", "created_at"),
        Index(
            "ix_conversation_artifacts_user_agent_created",
            "user_id",
            "agent_id",
            "created_at",
        ),
        Index(
            "ix_conversation_artifacts_user_kind_created",
            "user_id",
            "artifact_kind",
            "created_at",
        ),
        Index(
            "ix_conversation_artifacts_user_favorite_created",
            "user_id",
            "created_at",
            postgresql_where=text("is_favorite = true"),
            sqlite_where=text("is_favorite = 1"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    assistant_msg_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_call_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_tool_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    logical_path: Mapped[str] = mapped_column(String(500), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[str | None] = mapped_column(String(40), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    artifact_kind: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="other",
    )
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ready")
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )
    preview_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    download_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    branch_checkpoint_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linked_message_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
    )


class ArtifactVersion(Base):
    __tablename__ = "artifact_versions"
    __table_args__ = (
        UniqueConstraint("artifact_id", "version_number", name="uq_artifact_versions_number"),
        Index("ix_artifact_versions_artifact_created", "artifact_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation_artifacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_provider: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="local",
    )
    bucket: Mapped[str | None] = mapped_column(String(255), nullable=True)
    object_key: Mapped[str] = mapped_column(String(800), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
