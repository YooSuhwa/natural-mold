"""Real PostgreSQL proof for scheduler advisory-lock handoff."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_scheduler_leadership_is_exclusive_and_released_for_takeover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given two backend engines connected to one disposable PostgreSQL database.
    raw_url = os.environ.get("INTEGRATION_DATABASE_URL")
    if raw_url is None:
        pytest.skip("INTEGRATION_DATABASE_URL is required")
    parsed_url = make_url(raw_url)
    if not (parsed_url.database or "").startswith("moldy_pg_lane_"):
        pytest.fail("scheduler leadership requires a disposable PostgreSQL lane")
    async_url = parsed_url.set(drivername="postgresql+asyncpg")
    leader_engine = create_async_engine(async_url)
    contender_engine = create_async_engine(async_url)

    import app.scheduler as scheduler

    monkeypatch.setattr(scheduler, "engine", leader_engine)
    monkeypatch.setattr(scheduler, "_scheduler_leader_connection", None)

    try:
        # When the first backend acquires leadership.
        assert await scheduler.try_acquire_scheduler_leader() is True
        assert await scheduler.scheduler_leader_is_healthy() is True

        async with contender_engine.connect() as contender:
            blocked = bool(
                (
                    await contender.execute(
                        text("select pg_try_advisory_lock(:lock_id)"),
                        {"lock_id": scheduler._SCHEDULER_ADVISORY_LOCK_ID},
                    )
                ).scalar()
            )
            await contender.commit()

            # Then the second backend is fenced until the first releases the lock.
            assert blocked is False
            await scheduler.release_scheduler_leader()
            acquired_after_release = bool(
                (
                    await contender.execute(
                        text("select pg_try_advisory_lock(:lock_id)"),
                        {"lock_id": scheduler._SCHEDULER_ADVISORY_LOCK_ID},
                    )
                ).scalar()
            )
            assert acquired_after_release is True
            await contender.execute(
                text("select pg_advisory_unlock(:lock_id)"),
                {"lock_id": scheduler._SCHEDULER_ADVISORY_LOCK_ID},
            )
            await contender.commit()
    finally:
        await scheduler.release_scheduler_leader()
        await leader_engine.dispose()
        await contender_engine.dispose()
