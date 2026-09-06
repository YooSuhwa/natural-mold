from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.conversation_run import ConversationRun

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def _utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ConversationRunMetrics(Base):
    """One terminal, replay-safe metrics snapshot associated with a conversation run."""

    __tablename__ = "conversation_run_metrics"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    run: Mapped[ConversationRun] = relationship(back_populates="metrics")
    terminal_state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    elapsed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    ttft_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    generation_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tokens_per_second: Mapped[float | None] = mapped_column(Float, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_creation_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    usage_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    root_tool_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    descendant_tool_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    root_subagent_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    descendant_subagent_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    activity_json: Mapped[list[JsonValue]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    activity_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        default=_utc_now_naive,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        default=_utc_now_naive,
        onupdate=_utc_now_naive,
    )
