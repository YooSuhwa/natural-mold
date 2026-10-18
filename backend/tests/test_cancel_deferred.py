"""Cancel-deferred DB steps and the unit engine surviving a replaced connection."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.models.user import User
from app.services.cancel_deferred import finish_then_cancel
from tests.conftest import TEST_USER_ID
from tests.sqlite_test_engine import TestSession, engine


async def test_cancel_waits_for_the_step_in_flight_then_propagates() -> None:
    started, release, log = asyncio.Event(), asyncio.Event(), []

    async def step() -> str:
        started.set()
        await release.wait()
        log.append("finished")
        return "done"

    task = asyncio.create_task(finish_then_cancel(step()))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()  # the cancel is held while the step runs
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert log == ["finished"]


async def test_step_result_and_failure_pass_through() -> None:
    async def ok() -> int:
        return 7

    async def boom() -> None:
        raise ValueError("x")

    assert await finish_then_cancel(ok()) == 7
    with pytest.raises(ValueError, match="x"):
        await finish_then_cancel(boom())


async def test_replaced_connection_keeps_tables_and_rows() -> None:
    async with TestSession() as db:
        db.add(User(id=TEST_USER_ID, email="t@test.com", name="T"))
        await db.commit()
    async with engine.connect() as conn:
        (await conn.get_raw_connection()).invalidate()  # what a mid-statement cancel does
    async with TestSession() as db:
        assert await db.scalar(select(User.email).where(User.id == TEST_USER_ID)) == "t@test.com"
