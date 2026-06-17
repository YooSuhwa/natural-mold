from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.skill import Skill
    from app.models.user import User

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

SKILL_BUILDER_MODES = ("create", "improve")
SKILL_BUILDER_STATUSES = (
    "collecting",
    "drafting",
    "review",
    "confirming",
    "completed",
    "failed",
    "cancelled",
)


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SkillBuilderSession(Base):
    __tablename__ = "skill_builder_sessions"
    __table_args__ = (
        Index("ix_skill_builder_sessions_user_updated", "user_id", "updated_at"),
        Index("ix_skill_builder_sessions_finalized_skill", "finalized_skill_id"),
        Index("ix_skill_builder_sessions_source_skill", "source_skill_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_request: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="create",
        server_default="create",
    )
    source_skill_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("skills.id", ondelete="SET NULL"),
        nullable=True,
    )
    base_skill_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    base_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_snapshot: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="collecting",
        server_default="collecting",
    )
    current_phase: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    messages: Mapped[list[JsonValue] | None] = mapped_column(JSON, nullable=True)
    intent: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, nullable=True)
    draft_package: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, nullable=True)
    validation_result: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, nullable=True)
    compatibility_result: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    changelog_draft: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, nullable=True)
    eval_result: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, nullable=True)
    trigger_eval_result: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, nullable=True)
    finalized_skill_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("skills.id", ondelete="SET NULL"),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default=utc_now_naive,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default=utc_now_naive,
        onupdate=utc_now_naive,
        nullable=False,
    )

    user: Mapped[User] = relationship()
    source_skill: Mapped[Skill | None] = relationship(foreign_keys=[source_skill_id])
    finalized_skill: Mapped[Skill | None] = relationship(foreign_keys=[finalized_skill_id])
