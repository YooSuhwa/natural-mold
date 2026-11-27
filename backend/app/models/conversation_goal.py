"""A conversation goal, its version-bound evaluations and durable notification intents."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.conversation_run import utc_now_naive
from app.models.resource_scope import RequiredOrgScope, resource_scope_constraints


class ConversationGoal(RequiredOrgScope, Base):
    __tablename__ = "conversation_goals"
    __table_args__ = (
        *resource_scope_constraints("conversation_goals", system=False),
        UniqueConstraint("conversation_id", name="uq_conversation_goals_conversation"),
        CheckConstraint(
            "status IN ('active','paused','blocked','completed','canceled')",
            name="ck_conversation_goals_status",
        ),
        CheckConstraint(
            "version >= 1 AND revision >= 1 AND auto_followups >= 0 "
            "AND no_progress_count >= 0 AND latest_steer_seq >= 0 AND baseline_steer_seq >= 0",
            name="ck_conversation_goals_counters",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(500))
    criteria: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="active")
    auto_followups: Mapped[int] = mapped_column(Integer, default=0)
    no_progress_count: Mapped[int] = mapped_column(Integer, default=0)
    baseline_steer_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    latest_steer_seq: Mapped[int] = mapped_column(Integer, default=0)
    last_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    blocked_reason: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), default=utc_now_naive, onupdate=utc_now_naive
    )


class GoalAttempt(Base):
    __tablename__ = "goal_attempts"
    __table_args__ = (
        UniqueConstraint("goal_id", "goal_version", "run_id", name="uq_goal_attempts_evaluation"),
        UniqueConstraint(
            "goal_id", "goal_version", "followup_ordinal", name="uq_goal_attempts_followup"
        ),
        UniqueConstraint("followup_input_id", name="uq_goal_attempts_input"),
        CheckConstraint(
            "status IN ('pending','completed','followup_reserved','superseded','blocked')",
            name="ck_goal_attempts_status",
        ),
        CheckConstraint(
            "goal_version >= 1 AND steer_seq >= 0 AND "
            "(followup_ordinal IS NULL OR followup_ordinal > 0)",
            name="ck_goal_attempts_counters",
        ),
        Index("ix_goal_attempts_run", "run_id"),
        Index("ix_goal_attempts_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    goal_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_goals.id", ondelete="CASCADE"))
    goal_version: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_runs.id", ondelete="CASCADE"))
    requires_fresh_run: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    steer_seq: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    followup_ordinal: Mapped[int | None] = mapped_column(Integer)
    followup_input_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversation_run_inputs.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), default=utc_now_naive, onupdate=utc_now_naive
    )


class GoalTransitionIntent(Base):
    __tablename__ = "goal_transition_intents"
    __table_args__ = (
        UniqueConstraint(
            "goal_id",
            "goal_version",
            "goal_revision",
            "kind",
            name="uq_goal_transition_intents_event",
        ),
        CheckConstraint(
            "kind IN ('completed','blocked','approval_requested')",
            name="ck_goal_transition_intents_kind",
        ),
        CheckConstraint(
            "goal_version >= 1 AND goal_revision >= 1", name="ck_goal_transition_intents_version"
        ),
        Index("ix_goal_transition_intents_created", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    goal_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_goals.id", ondelete="CASCADE"))
    goal_version: Mapped[int] = mapped_column(Integer)
    goal_revision: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_runs.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utc_now_naive)
