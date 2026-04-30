"""Per-model daily spend aggregate (one row per ``(date, model_id)``).

Sibling of :class:`DailySpendUser` / :class:`DailySpendAgent`. The CASCADE on
``model_id`` lets an admin retire a row from the catalog without leaving
orphan aggregates; raw ``token_usages`` rows still record ``model_name`` as a
string so historical totals are reconstructible.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from datetime import date as date_type
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DailySpendModel(Base):
    __tablename__ = "daily_spend_model"
    __table_args__ = (
        UniqueConstraint("date", "model_id", name="uq_daily_spend_model_date_model"),
        Index("ix_daily_spend_model_date", "date"),
        Index("ix_daily_spend_model_model_date", "model_id", "date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("models.id", ondelete="CASCADE"), nullable=False
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


__all__ = ["DailySpendModel"]
