"""A prepared recall's optional comparison follows the owning transaction's receipt."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.memory import MemoryRecall, MemoryRecallRun
from app.services import memory_embedder, memory_recall, memory_recall_shadow
from tests.sqlite_test_engine import engine
from tests.test_memory_recall import _count, _embed, _run, _save
from tests.test_memory_recall_shadow import (
    CONTEXT,
    KEYWORD_FACT,
    QUESTION,
    _drain_shadow,
    _observations,
)
from tests.test_memory_recall_shadow import approved as approved  # noqa: F401 (pytest fixture)
from tests.test_memory_service import _seed_agent

pytestmark = pytest.mark.asyncio


@dataclass
class PreparedCase:
    sessions: async_sessionmaker[AsyncSession]
    run_id: str
    fact_id: UUID
    filters: memory_recall.Filters
    schedule: Mock
    embed: AsyncMock

    async def prepare(self, owner: AsyncSession) -> memory_recall.RunRecall:
        return await memory_recall.prepared_recall_for_run(
            owner, self.sessions, CONTEXT, run_id=self.run_id, filters=self.filters
        )


@pytest.fixture
async def case(
    db: AsyncSession,
    approved: memory_embedder.OpenAIShapedEmbedder,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[PreparedCase]:
    agent, conversation = await _seed_agent(db)
    fact = await _save(db, KEYWORD_FACT)
    await _embed(db, fact, [1.0, 0.0, 0.0], memory_embedder.vector_model_key(approved))
    run = await _run(db, conversation, agent, QUESTION)
    monkeypatch.setattr(memory_embedder, "resolve_embedder", AsyncMock(return_value=approved))
    embed = AsyncMock(return_value=[memory_embedder.normalized([1, 0, 0])])
    monkeypatch.setattr(memory_embedder.OpenAIShapedEmbedder, "embed", embed)
    schedule = Mock(wraps=memory_recall_shadow.schedule)
    monkeypatch.setattr(memory_recall_shadow, "schedule", schedule)

    # Use the same named SQLite database, on separate physical connections. Closing the
    # ranking reader must never roll back the owner session or invalidate its savepoint.
    readers = create_async_engine(engine.url, poolclass=NullPool)
    try:
        yield PreparedCase(
            async_sessionmaker(readers, expire_on_commit=False),
            str(run.id),
            fact.id,
            memory_recall.Filters("both", agent),
            schedule,
            embed,
        )
    finally:
        await _drain_shadow()
        await readers.dispose()


@pytest.mark.parametrize("in_savepoint", [False, True])
async def test_prepared_shadow_runs_once_after_outer_commit(
    db: AsyncSession, case: PreparedCase, caplog: pytest.LogCaptureFixture, in_savepoint: bool
) -> None:
    caplog.set_level(logging.INFO, logger=memory_recall_shadow.__name__)
    # Python SQLite's legacy transaction mode otherwise releases its first savepoint
    # as a commit when no DML has started the physical transaction yet.
    await db.execute(text("BEGIN"))
    enclosing = await db.begin_nested() if in_savepoint else None
    result = await case.prepare(db)
    assert result.mode == "fresh" and [row.id for row in result.records] == [case.fact_id]
    assert await _count(db, MemoryRecallRun) == 1
    if enclosing is not None:
        await enclosing.commit()
    # Savepoints unrelated to the receipt can end either way during preparation.
    async with db.begin_nested():
        await db.scalar(select(1))
    savepoint = await db.begin_nested()
    await db.scalar(select(1))
    await savepoint.rollback()
    case.schedule.assert_not_called()
    case.embed.assert_not_awaited()

    await db.commit()
    await _drain_shadow()
    case.schedule.assert_called_once()
    case.embed.assert_awaited_once()
    [event] = _observations(caplog)
    assert event["status"] == "completed" and event["overlap_count"] == 1

    reused = await case.prepare(db)
    assert reused.mode == "reused" and [row.id for row in reused.records] == [case.fact_id]
    await db.commit()
    await _drain_shadow()
    assert list(await db.scalars(select(MemoryRecall.record_id))) == [case.fact_id]
    assert case.schedule.call_count == case.embed.await_count == 1


@pytest.mark.parametrize("rolled_back", ["owner", "savepoint"])
async def test_rolled_back_preparation_is_discarded_and_next_commit_cannot_replay_it(
    db: AsyncSession, case: PreparedCase, rolled_back: str
) -> None:
    savepoint = await db.begin_nested() if rolled_back == "savepoint" else None
    result = await case.prepare(db)
    assert result.mode == "fresh" and [row.id for row in result.records] == [case.fact_id]
    case.schedule.assert_not_called()
    if savepoint is not None:
        await savepoint.rollback()
    else:
        await db.rollback()

    # Reuse the same owner for an unrelated transaction, then retry the original run.
    assert await _count(db, MemoryRecallRun) == 0
    assert await _count(db, MemoryRecall) == 0
    await db.commit()
    await _drain_shadow()
    case.schedule.assert_not_called()
    case.embed.assert_not_awaited()

    assert (await case.prepare(db)).mode == "fresh"
    await db.commit()
    await _drain_shadow()
    case.schedule.assert_called_once()
    case.embed.assert_awaited_once()
