"""Batch UPSERT of buffered spend into ``daily_spend_*`` (moved from ``spend_writer``).

Rows are keyed ``(org_id, date, target)`` (F-14, plan 6.2/8.7): spend is booked per
organization, and ``tenant_id`` rides along for company totals. PostgreSQL and SQLite
use native ``ON CONFLICT ... DO UPDATE``; other dialects fall back to read-modify-write.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import date as date_type
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import is_postgres
from app.models.daily_spend_agent import DailySpendAgent
from app.models.daily_spend_model import DailySpendModel
from app.models.daily_spend_user import DailySpendUser

# (date, org_id, tenant_id, target_id)
type BucketKey = tuple[date_type, uuid.UUID | None, uuid.UUID | None, uuid.UUID]


@dataclass
class Bucket:
    """Pre-flush in-memory aggregator for one ``BucketKey``."""

    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    request_count: int = 0


AXES: tuple[tuple[type[Any], str], ...] = (
    (DailySpendUser, "user_id"),
    (DailySpendAgent, "agent_id"),
    (DailySpendModel, "model_id"),
)


def _rows(target_col: str, buckets: dict[BucketKey, Bucket], now: datetime) -> list[dict[str, Any]]:
    return [
        {
            "id": uuid.uuid4(),
            "date": the_date,
            "org_id": org_id,
            "tenant_id": tenant_id,
            target_col: target_id,
            "total_tokens_in": bucket.tokens_in,
            "total_tokens_out": bucket.tokens_out,
            "total_cost_usd": bucket.cost_usd,
            "request_count": bucket.request_count,
            "updated_at": now,
        }
        for (the_date, org_id, tenant_id, target_id), bucket in buckets.items()
    ]


async def upsert_axis(
    db: AsyncSession, table_cls: type[Any], target_col: str, buckets: dict[BucketKey, Bucket]
) -> None:
    """Dialect-aware UPSERT: PostgreSQL native, SQLite via ON CONFLICT."""

    if not buckets:
        return
    now = datetime.now(UTC).replace(tzinfo=None)
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    if is_postgres(db):
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        await _upsert_application_level(db, table_cls, target_col, buckets, now)
        return
    stmt = insert(table_cls).values(_rows(target_col, buckets, now))
    stmt = stmt.on_conflict_do_update(
        index_elements=["org_id", "date", target_col],
        set_={
            "total_tokens_in": table_cls.total_tokens_in + stmt.excluded.total_tokens_in,
            "total_tokens_out": table_cls.total_tokens_out + stmt.excluded.total_tokens_out,
            "total_cost_usd": table_cls.total_cost_usd + stmt.excluded.total_cost_usd,
            "request_count": table_cls.request_count + stmt.excluded.request_count,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    await db.execute(stmt)


async def _upsert_application_level(
    db: AsyncSession,
    table_cls: type[Any],
    target_col: str,
    buckets: dict[BucketKey, Bucket],
    now: datetime,
) -> None:
    """Last-resort UPSERT for dialects without native ON CONFLICT support."""

    for (the_date, org_id, tenant_id, target_id), bucket in buckets.items():
        existing = await db.execute(
            select(table_cls).where(
                table_cls.date == the_date,
                table_cls.org_id.is_(None) if org_id is None else table_cls.org_id == org_id,
                getattr(table_cls, target_col) == target_id,
            )
        )
        row = existing.scalar_one_or_none()
        if row is None:
            db.add(
                table_cls(
                    date=the_date,
                    org_id=org_id,
                    tenant_id=tenant_id,
                    **{target_col: target_id},
                    total_tokens_in=bucket.tokens_in,
                    total_tokens_out=bucket.tokens_out,
                    total_cost_usd=bucket.cost_usd,
                    request_count=bucket.request_count,
                    updated_at=now,
                )
            )
        else:
            row.total_tokens_in += bucket.tokens_in
            row.total_tokens_out += bucket.tokens_out
            row.total_cost_usd = (row.total_cost_usd or Decimal("0")) + bucket.cost_usd
            row.request_count += bucket.request_count
            row.updated_at = now


__all__ = ["AXES", "Bucket", "BucketKey", "upsert_axis"]
