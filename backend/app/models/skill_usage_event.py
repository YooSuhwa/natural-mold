"""Skill-axis usage ledger (Phase 3, D3 — honest attribution only).

Two source kinds:

* ``evaluation_run`` — real LLM tokens/cost of a skill evaluation run. The
  whole run exists for exactly one skill, so full attribution is exact.
  ``cost_usd`` stays NULL when the runner model has no pricing (NULL means
  "unknown", not "free").
* ``chat_execution`` — one ``execute_in_skill`` sandbox execution in chat.
  Scripts consume no LLM tokens themselves, so only ``execution_count``
  carries signal. Whole-conversation LLM cost is deliberately NOT attributed
  (multi-skill double counting / unrelated turns — spec §2 D3).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.skill_builder_session import utc_now_naive

SKILL_USAGE_SOURCE_KINDS = ("evaluation_run", "chat_execution")


class SkillUsageEvent(Base):
    __tablename__ = "skill_usage_events"
    __table_args__ = (
        Index("ix_skill_usage_events_skill_created", "skill_id", "created_at"),
        Index("ix_skill_usage_events_evaluation_run", "evaluation_run_id"),
        # Referential-action FK columns — indexed so user (CASCADE) and
        # conversation (SET NULL) deletion don't full-scan this ledger.
        Index("ix_skill_usage_events_user", "user_id"),
        Index("ix_skill_usage_events_conversation", "conversation_id"),
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
    source_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    evaluation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("skill_evaluation_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    model_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    execution_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default=utc_now_naive,
        nullable=False,
    )
