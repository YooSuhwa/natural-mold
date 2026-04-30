"""Per-user daily spend aggregate (one row per ``(date, user_id)``).

This is the user-axis sibling of :class:`DailySpendAgent` /
:class:`DailySpendModel`. The :class:`app.services.spend_writer.SpendEntry`
queue performs an UPSERT on the unique ``(date, user_id)`` key so concurrent
agent calls accumulate into a single daily row rather than hammering the DB
with one INSERT per request. The ``token_usages`` table still records every
raw event for audit; this aggregate is the read surface for dashboards.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from datetime import date as date_type
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DailySpendUser(Base):
    __tablename__ = "daily_spend_user"
    __table_args__ = (
        UniqueConstraint("date", "user_id", name="uq_daily_spend_user_date_user"),
        Index("ix_daily_spend_user_date", "date"),
        Index("ix_daily_spend_user_user_date", "user_id", "date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    total_tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Numeric(20, 8) keeps fractional cents precise for low-cost models
    # (Anthropic Haiku ≈ $0.0000003 / token). The ORM surface returns Decimal.
    total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), nullable=False, default=Decimal("0")
    )
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )


__all__ = ["DailySpendUser"]
