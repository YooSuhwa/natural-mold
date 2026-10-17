"""H-07 (plan 8.8/8.9): spend queue items carry tenant_id; a flush never mixes companies."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SCOPE_INFO_KEY, session_tenant
from app.models.daily_spend_user import DailySpendUser
from app.services import spend_flush
from app.services.spend_upsert import Bucket, BucketKey
from app.services.spend_writer import DailySpendUpdateQueue, SpendEntry
from tests.conftest import TestSession

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
DAY = date(2026, 10, 1)


def _entry(tenant_id: uuid.UUID, user_id: uuid.UUID | None = None) -> SpendEntry:
    return SpendEntry(
        DAY,
        user_id or uuid.uuid4(),
        3,
        2,
        Decimal("0.25"),
        org_id=uuid.uuid4(),
        tenant_id=tenant_id,
    )


def _queue() -> DailySpendUpdateQueue:
    queue = DailySpendUpdateQueue()
    queue._session_factory = TestSession  # type: ignore[assignment]
    return queue


def test_spend_entries_require_a_tenant_id() -> None:
    with pytest.raises(TypeError):
        SpendEntry(DAY, uuid.uuid4(), 1, 1, Decimal("0"))  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="tenant_id"):
        SpendEntry(DAY, uuid.uuid4(), 1, 1, Decimal("0"), tenant_id=None)  # type: ignore[arg-type]


async def test_mixed_company_batch_flushes_one_company_per_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[uuid.UUID | None, set[uuid.UUID | None]]] = []
    original = spend_flush.upsert_axis

    async def recording_upsert(
        db: AsyncSession, table_cls: type[Any], target_col: str, buckets: dict[BucketKey, Bucket]
    ) -> None:
        calls.append((session_tenant(db), {key[2] for key in buckets}))
        await original(db, table_cls, target_col, buckets)

    monkeypatch.setattr(spend_flush, "upsert_axis", recording_upsert)
    entries = [_entry(TENANT_A), _entry(TENANT_B), _entry(TENANT_A), _entry(TENANT_B)]
    await _queue()._flush_batch(entries)

    user_axis = [(scope, tenants) for scope, tenants in calls if tenants]
    assert {scope for scope, _ in user_axis} == {TENANT_A, TENANT_B}
    assert all(tenants == {scope} for scope, tenants in user_axis)
    async with TestSession() as db:
        rows = (await db.scalars(select(DailySpendUser).where(DailySpendUser.date == DAY))).all()
    by_user = {row.user_id: row.tenant_id for row in rows}
    assert {by_user[entry.user_id] for entry in entries} == {TENANT_A, TENANT_B}
    assert all(by_user[entry.user_id] == entry.tenant_id for entry in entries)


async def test_flush_sessions_are_tenant_bound_never_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scopes: list[object] = []

    def maker() -> AsyncSession:
        session = TestSession()
        scopes.append(session.info)
        return session

    await spend_flush.flush_by_tenant([_entry(TENANT_A), _entry(TENANT_B)], maker)
    bound = [info[SCOPE_INFO_KEY] for info in scopes if isinstance(info, dict)]
    assert sorted(scope["app.tenant_id"] for scope in bound) == sorted(
        [str(TENANT_A), str(TENANT_B)]
    )
    assert all("app.scope" not in scope for scope in bound)


async def test_one_failing_company_does_not_drop_another_companys_spend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = spend_flush.upsert_axis

    async def failing_for_a(
        db: AsyncSession, table_cls: type[Any], target_col: str, buckets: dict[BucketKey, Bucket]
    ) -> None:
        if session_tenant(db) == TENANT_A:
            raise RuntimeError("company A database failure")
        await original(db, table_cls, target_col, buckets)

    monkeypatch.setattr(spend_flush, "upsert_axis", failing_for_a)
    a_entry, b_entry = _entry(TENANT_A), _entry(TENANT_B)
    with pytest.raises(RuntimeError, match="company A"):
        await _queue()._flush_batch([a_entry, b_entry])
    async with TestSession() as db:
        users = set(
            await db.scalars(select(DailySpendUser.user_id).where(DailySpendUser.date == DAY))
        )
    assert b_entry.user_id in users
    assert a_entry.user_id not in users
