"""Process-local scheduler advisory-lock contracts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.elements import TextClause


@pytest.mark.asyncio
async def test_leader_health_releases_connection_when_advisory_lock_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a live PostgreSQL connection whose advisory lock is no longer held.
    import app.scheduler as scheduler

    result = MagicMock()
    result.scalar.return_value = False
    connection = AsyncMock()
    connection.execute.return_value = result
    postgres_engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    monkeypatch.setattr(scheduler, "engine", postgres_engine)
    monkeypatch.setattr(scheduler, "_scheduler_leader_connection", connection)

    # When the leader validates its lock ownership.
    is_healthy = await scheduler.scheduler_leader_is_healthy()

    # Then leadership is rejected and the stale connection is released.
    assert is_healthy is False
    connection.commit.assert_awaited_once()
    connection.close.assert_awaited_once()
    assert scheduler._scheduler_leader_connection is None


@pytest.mark.asyncio
async def test_leader_acquisition_retries_after_temporary_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given PostgreSQL is temporarily unreachable during a takeover attempt.
    import app.scheduler as scheduler

    postgres_engine = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"),
        connect=AsyncMock(side_effect=SQLAlchemyError("temporary outage")),
    )
    monkeypatch.setattr(scheduler, "engine", postgres_engine)
    monkeypatch.setattr(scheduler, "_scheduler_leader_connection", None)

    # When this process tries to acquire scheduler leadership.
    acquired = await scheduler.try_acquire_scheduler_leader()

    # Then it stays a nonleader so the monitor can retry on its next tick.
    assert acquired is False
    assert scheduler._scheduler_leader_connection is None


@pytest.mark.asyncio
async def test_concurrent_leader_health_checks_share_connection_serially(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given monitor and scheduled-trigger checks targeting the same leader connection.
    import app.scheduler as scheduler

    first_entered = anyio.Event()
    second_started = anyio.Event()
    release_queries = anyio.Event()
    active_queries = 0
    max_active_queries = 0

    class Connection:
        async def execute(
            self,
            _statement: TextClause,
            _parameters: dict[str, int],
        ) -> MagicMock:
            nonlocal active_queries, max_active_queries
            active_queries += 1
            max_active_queries = max(max_active_queries, active_queries)
            first_entered.set()
            await release_queries.wait()
            active_queries -= 1
            result = MagicMock()
            result.scalar.return_value = True
            return result

        async def commit(self) -> None:
            return None

        async def close(self) -> None:
            return None

    connection = Connection()
    postgres_engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    monkeypatch.setattr(scheduler, "engine", postgres_engine)
    monkeypatch.setattr(scheduler, "_scheduler_leader_connection", connection)

    async def run_second_health_check() -> None:
        second_started.set()
        await scheduler.scheduler_leader_is_healthy()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(scheduler.scheduler_leader_is_healthy)
        await first_entered.wait()
        task_group.start_soon(run_second_health_check)
        await second_started.wait()
        release_queries.set()

    # Then SQLAlchemy never receives concurrent operations on one AsyncConnection.
    assert max_active_queries == 1
