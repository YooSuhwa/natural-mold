from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.conversation_run import utc_now_naive

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class ConversationHistoryManifest(Base):
    """Versioned completeness record for sanitized retained history."""

    __tablename__ = "conversation_history_manifests"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "schema_version",
            name="uq_conversation_history_manifests_version",
        ),
        CheckConstraint("schema_version = 1", name="ck_conversation_history_manifests_version"),
        CheckConstraint(
            "state IN ('building', 'complete')",
            name="ck_conversation_history_manifests_state",
        ),
        CheckConstraint(
            "checkpoint_count >= 0 AND event_count >= 0",
            name="ck_conversation_history_manifests_counts",
        ),
        CheckConstraint(
            "length(canonical_payload_sha256) = 64",
            name="ck_conversation_history_manifests_checksum",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    source_agent_id_snapshot: Mapped[uuid.UUID] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="building")
    checkpoint_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    canonical_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utc_now_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )


class ConversationHistoryCheckpoint(Base):
    """Sanitized materialization of one retained checkpoint."""

    __tablename__ = "conversation_history_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "manifest_id",
            "checkpoint_id",
            "checkpoint_namespace",
            name="uq_conversation_history_checkpoints_identity",
        ),
        CheckConstraint(
            "length(payload_sha256) = 64",
            name="ck_conversation_history_checkpoints_checksum",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation_history_manifests.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    checkpoint_id: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    checkpoint_namespace: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    channel_values: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)
    messages: Mapped[list[JsonValue]] = mapped_column(JSON, nullable=False)
    branch: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)
    trace: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utc_now_naive, nullable=False
    )


class ConversationHistoryEvent(Base):
    """Sanitized retained event projection for one assistant message range."""

    __tablename__ = "conversation_history_events"
    __table_args__ = (
        UniqueConstraint(
            "manifest_id",
            "assistant_message_id",
            "sequence_start",
            name="uq_conversation_history_events_range",
        ),
        CheckConstraint(
            "sequence_start >= 0 AND sequence_end >= sequence_start",
            name="ck_conversation_history_events_sequence",
        ),
        CheckConstraint(
            "length(payload_sha256) = 64",
            name="ck_conversation_history_events_checksum",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation_history_manifests.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    assistant_message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    sequence_start: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_end: Mapped[int] = mapped_column(Integer, nullable=False)
    events: Mapped[list[JsonValue]] = mapped_column(JSON, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=utc_now_naive, nullable=False
    )


__all__ = [
    "ConversationHistoryCheckpoint",
    "ConversationHistoryEvent",
    "ConversationHistoryManifest",
]
