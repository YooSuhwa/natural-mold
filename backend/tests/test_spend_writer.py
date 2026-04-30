"""Unit tests for ``app.services.spend_writer.DailySpendUpdateQueue``.

The queue is exercised against the in-memory aiosqlite DB so we don't need
PostgreSQL — the dialect-aware UPSERT path is hit through the SQLite branch
of ``_upsert``. A separate Postgres round-trip is covered by
``test_migration_m21.py``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date as date_type
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.daily_spend_agent import DailySpendAgent
from app.models.daily_spend_model import DailySpendModel
from app.models.daily_spend_user import DailySpendUser
from app.services.spend_writer import (
    DailySpendUpdateQueue,
    SpendEntry,
)
from tests.conftest import TestSession


@pytest.fixture
def queue() -> DailySpendUpdateQueue:
    """Fresh queue per test, wired to the test session factory."""

    q = DailySpendUpdateQueue(flush_interval_sec=0.1, batch_size=50, max_queue_size=1000)
    q._session_factory = TestSession  # type: ignore[assignment] — test-only override
    return q


def _entry(
    user_id: uuid.UUID,
    *,
    agent_id: uuid.UUID | None = None,
    model_id: uuid.UUID | None = None,
    tokens_in: int = 100,
    tokens_out: int = 50,
    cost: str = "0.0010",
    the_date: date_type | None = None,
) -> SpendEntry:
    return SpendEntry(
        date=the_date or date_type.today(),
        user_id=user_id,
        agent_id=agent_id,
        model_id=model_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=Decimal(cost),
    )


@pytest.mark.asyncio
async def test_flush_writes_to_all_three_axes(queue: DailySpendUpdateQueue) -> None:
    user_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    model_id = uuid.uuid4()

    entries = [
        _entry(user_id, agent_id=agent_id, model_id=model_id, tokens_in=10, tokens_out=5),
        _entry(user_id, agent_id=agent_id, model_id=model_id, tokens_in=20, tokens_out=15),
    ]
    await queue._flush_batch(entries)

    async with TestSession() as db:
        user_rows = (await db.execute(select(DailySpendUser))).scalars().all()
        assert len(user_rows) == 1
        assert user_rows[0].total_tokens_in == 30
        assert user_rows[0].total_tokens_out == 20

        agent_rows = (await db.execute(select(DailySpendAgent))).scalars().all()
        assert len(agent_rows) == 1
        assert agent_rows[0].request_count == 2

        model_rows = (await db.execute(select(DailySpendModel))).scalars().all()
        assert len(model_rows) == 1


@pytest.mark.asyncio
async def test_on_conflict_accumulates_existing_row(queue: DailySpendUpdateQueue) -> None:
    user_id = uuid.uuid4()
    today = date_type.today()

    await queue._flush_batch(
        [_entry(user_id, tokens_in=100, tokens_out=50, cost="0.0050", the_date=today)]
    )
    await queue._flush_batch(
        [_entry(user_id, tokens_in=200, tokens_out=100, cost="0.0100", the_date=today)]
    )

    async with TestSession() as db:
        rows = (await db.execute(select(DailySpendUser))).scalars().all()
        assert len(rows) == 1
        assert rows[0].total_tokens_in == 300
        assert rows[0].total_tokens_out == 150
        assert rows[0].total_cost_usd == Decimal("0.0150")
        assert rows[0].request_count == 2


@pytest.mark.asyncio
async def test_skips_axis_when_target_id_missing(queue: DailySpendUpdateQueue) -> None:
    """User axis always rolls up; agent / model axes need explicit ids."""

    user_id = uuid.uuid4()
    await queue._flush_batch(
        [_entry(user_id, agent_id=None, model_id=None)]
    )

    async with TestSession() as db:
        assert (await db.execute(select(DailySpendUser))).scalars().all()
        assert not (await db.execute(select(DailySpendAgent))).scalars().all()
        assert not (await db.execute(select(DailySpendModel))).scalars().all()


@pytest.mark.asyncio
async def test_loop_flushes_after_interval(queue: DailySpendUpdateQueue) -> None:
    """Background drain task fires within ~flush_interval seconds."""

    user_id = uuid.uuid4()
    await queue.start()
    try:
        queue.add(_entry(user_id))
        # Wait up to 1.5s for the loop to flush (interval is 0.1s).
        for _ in range(15):
            async with TestSession() as db:
                rows = (await db.execute(select(DailySpendUser))).scalars().all()
                if rows:
                    assert rows[0].total_tokens_in == 100
                    return
            await asyncio.sleep(0.1)
        pytest.fail("queue never drained within timeout")
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_stop_drains_remaining_entries(queue: DailySpendUpdateQueue) -> None:
    """``stop`` flushes whatever is buffered before cancelling the task."""

    user_id = uuid.uuid4()
    # Use a long flush interval so the loop won't fire on its own.
    queue._flush_interval = 30.0  # type: ignore[assignment]
    await queue.start()
    try:
        for _ in range(5):
            queue.add(_entry(user_id, tokens_in=1, tokens_out=1, cost="0.0001"))
        # Give the loop a beat to pick up the first entry.
        await asyncio.sleep(0.05)
    finally:
        await queue.stop()

    async with TestSession() as db:
        rows = (await db.execute(select(DailySpendUser))).scalars().all()
        assert len(rows) == 1
        assert rows[0].total_tokens_in == 5
        assert rows[0].request_count == 5


@pytest.mark.asyncio
async def test_add_outside_running_loop_is_noop() -> None:
    """``add`` from a sync context degrades to a warning instead of raising."""

    q = DailySpendUpdateQueue()
    # Calling without ``start`` and without an active loop binding
    # should not raise — but the test runner *is* running an event loop, so
    # we exercise the QueueFull degrade path instead by saturating it.
    q._max_queue_size = 1  # type: ignore[assignment]
    q._session_factory = TestSession  # type: ignore[assignment]
    q.add(_entry(uuid.uuid4()))
    # Second add should be silently dropped.
    q.add(_entry(uuid.uuid4()))


@pytest.mark.asyncio
async def test_distinct_dates_create_separate_rows(queue: DailySpendUpdateQueue) -> None:
    user_id = uuid.uuid4()
    yesterday = date_type.today().replace(day=max(1, date_type.today().day - 1))
    today = date_type.today()
    if yesterday == today:  # first of month edge — skip
        return

    await queue._flush_batch(
        [
            _entry(user_id, the_date=yesterday, tokens_in=10, tokens_out=5),
            _entry(user_id, the_date=today, tokens_in=20, tokens_out=10),
        ]
    )

    async with TestSession() as db:
        rows = (await db.execute(select(DailySpendUser))).scalars().all()
        assert len(rows) == 2


@pytest.mark.asyncio
async def test_decimal_cost_precision_preserved(queue: DailySpendUpdateQueue) -> None:
    user_id = uuid.uuid4()
    # Sub-cent precision (Anthropic Haiku territory).
    await queue._flush_batch(
        [
            _entry(user_id, cost="0.00000037"),
            _entry(user_id, cost="0.00000063"),
        ]
    )
    async with TestSession() as db:
        rows = (await db.execute(select(DailySpendUser))).scalars().all()
        assert len(rows) == 1
        # 0.00000037 + 0.00000063 = 0.00000100
        assert rows[0].total_cost_usd == Decimal("0.00000100")
