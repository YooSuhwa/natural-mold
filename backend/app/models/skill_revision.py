from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.skill_builder_session import JsonValue, utc_now_naive

if TYPE_CHECKING:
    from app.models.skill import Skill
    from app.models.skill_builder_session import SkillBuilderSession
    from app.models.user import User

SKILL_REVISION_OPERATIONS = (
    "create",
    "manual_metadata_update",
    "manual_content_update",
    "manual_file_update",
    "builder_create",
    "builder_improvement",
    "rollback",
)


class SkillRevision(Base):
    __tablename__ = "skill_revisions"
    __table_args__ = (
        UniqueConstraint("skill_id", "revision_number", name="uq_skill_revisions_number"),
        Index("ix_skill_revisions_skill_created", "skill_id", "created_at"),
        Index("ix_skill_revisions_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("skill_builder_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    parent_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("skill_revisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    restored_from_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("skill_revisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    skill_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage_provider: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="local",
        server_default="local",
    )
    object_key: Mapped[str] = mapped_column(String(800), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    changed_files: Mapped[list[JsonValue] | None] = mapped_column(JSON, nullable=True)
    changelog_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    changelog_items: Mapped[list[JsonValue] | None] = mapped_column(JSON, nullable=True)
    compatibility_result: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    evaluation_summary: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict[str, JsonValue]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default=utc_now_naive,
        nullable=False,
    )

    skill: Mapped[Skill] = relationship()
    user: Mapped[User] = relationship()
    source_session: Mapped[SkillBuilderSession | None] = relationship()
