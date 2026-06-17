from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.skill_builder_session import JsonValue, utc_now_naive

if TYPE_CHECKING:
    from app.models.skill import Skill
    from app.models.user import User

SKILL_EVALUATION_RUN_STATUSES = (
    "queued",
    "running",
    "grading",
    "completed",
    "failed",
    "cancelled",
)


class SkillEvaluationSet(Base):
    __tablename__ = "skill_evaluation_sets"
    __table_args__ = (
        Index("ix_skill_evaluation_sets_skill_updated", "skill_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_kind: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="builder",
        server_default="builder",
    )
    template_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    template_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    generation_strategy: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    evals: Mapped[list[JsonValue]] = mapped_column(JSON, nullable=False)
    expectations_schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
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
    skill: Mapped[Skill] = relationship()


class SkillEvaluationRun(Base):
    __tablename__ = "skill_evaluation_runs"
    __table_args__ = (
        Index("ix_skill_evaluation_runs_skill_created", "skill_id", "created_at"),
        Index("ix_skill_evaluation_runs_set_created", "evaluation_set_id", "created_at"),
        Index("ix_skill_evaluation_runs_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
    )
    evaluation_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skill_evaluation_sets.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="queued",
        server_default="queued",
    )
    skill_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    skill_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runner_model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    runner_version: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="1",
        server_default="1",
    )
    grader_prompt_version: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="1",
        server_default="1",
    )
    eval_schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    run_config: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, nullable=True)
    estimate: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, nullable=True)
    benchmark: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON, nullable=True)
    case_results: Mapped[list[JsonValue] | None] = mapped_column(JSON, nullable=True)
    artifact_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )
    cancellation_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
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
    skill: Mapped[Skill] = relationship()
    evaluation_set: Mapped[SkillEvaluationSet] = relationship()
