"""Per-agent daily spend aggregate (one row per ``(date, agent_id)``).

Sibling of :class:`DailySpendUser` / :class:`DailySpendModel`. The CASCADE on
``agent_id`` means deleting an agent purges its rolled-up history — the raw
``token_usages`` rows still survive (separate FK chain) so audit isn't lost.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from datetime import date as date_type
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DailySpendAgent(Base):
    __tablename__ = "daily_spend_agent"
    __table_args__ = (
        UniqueConstraint("date", "agent_id", name="uq_daily_spend_agent_date_agent"),
        Index("ix_daily_spend_agent_date", "date"),
        Index("ix_daily_spend_agent_agent_date", "agent_id", "date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )

    total_tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), nullable=False, default=Decimal("0")
    )
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )


__all__ = ["DailySpendAgent"]
