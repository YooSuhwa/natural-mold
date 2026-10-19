"""Per-company flush of buffered spend entries (plan 8.8 queue items, H-07).

A drained batch is split by ``tenant_id`` and each company's slice is UPSERTed in that
company's own session and transaction, so no database transaction mixes companies.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionTenant, async_session, tenant_session
from app.services.spend_upsert import AXES, Bucket, BucketKey, upsert_axis

if TYPE_CHECKING:
    from app.services.spend_writer import SpendEntry

logger = logging.getLogger(__name__)


def coerce_cost(value: object) -> Decimal:
    """Store Decimal; ``str()`` keeps float inputs free of binary-float noise."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    return Decimal("0")


def group_by_tenant(entries: Sequence[SpendEntry]) -> dict[uuid.UUID, list[SpendEntry]]:
    groups: dict[uuid.UUID, list[SpendEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.tenant_id, []).append(entry)
    return groups


def bucket_entries(entries: Sequence[SpendEntry]) -> tuple[dict[BucketKey, Bucket], ...]:
    buckets: tuple[dict[BucketKey, Bucket], ...] = tuple({} for _ in AXES)
    for entry in entries:
        cost = coerce_cost(entry.cost_usd)
        targets = (entry.user_id, entry.agent_id, entry.model_id)
        for axis_buckets, target_id in zip(buckets, targets, strict=True):
            if target_id is None:
                continue
            key = (entry.date, entry.org_id, entry.tenant_id, target_id)
            bucket = axis_buckets.setdefault(key, Bucket())
            bucket.tokens_in += entry.tokens_in
            bucket.tokens_out += entry.tokens_out
            bucket.cost_usd += cost
            bucket.request_count += entry.request_count
    return buckets


async def flush_tenant(
    tenant_id: uuid.UUID,
    entries: Sequence[SpendEntry],
    session_factory: Callable[[], AsyncSession] | None = None,
) -> None:
    buckets = bucket_entries(entries)
    scope = SessionTenant(tenant_id)
    async with tenant_session(scope, maker=session_factory or async_session) as db:
        try:
            for (table_cls, target_col), axis_buckets in zip(AXES, buckets, strict=True):
                await upsert_axis(db, table_cls, target_col, axis_buckets)
            await db.commit()
        except Exception:  # noqa: BLE001 — rolled back, re-raised to the caller
            await db.rollback()
            raise


async def flush_by_tenant(
    entries: Sequence[SpendEntry], session_factory: Callable[[], AsyncSession] | None = None
) -> None:
    """One transaction per company; a failing company does not block the others."""
    failure: Exception | None = None
    for tenant_id, tenant_entries in group_by_tenant(entries).items():
        try:
            await flush_tenant(tenant_id, tenant_entries, session_factory)
        except Exception as exc:  # noqa: BLE001 — flush the remaining companies first
            logger.warning("spend flush failed tenant_id=%s", tenant_id, exc_info=True)
            failure = failure or exc
    if failure is not None:
        raise failure
