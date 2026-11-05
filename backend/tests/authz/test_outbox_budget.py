"""apply_now's wall-clock budget (8.6 item 2): a slow store leaves the rows to the worker.

The suite raises the budget for every other test (tests/conftest.py); this one sets it short.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.authz import outbox
from app.authz.outbox import OutboxRun, TupleOp, apply_now, enqueue
from app.authz.tuple_types import RelationTuple
from app.models.authz import AuthzOutbox
from tests.conftest import TestSession


class HangingStore:
    async def write(self, tuples: Sequence[RelationTuple]) -> None:
        await asyncio.sleep(3600)

    async def delete(self, tuples: Sequence[RelationTuple]) -> None:
        await asyncio.sleep(3600)


async def test_a_slow_store_times_out_and_leaves_the_row_pending(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    now = datetime.now(UTC)
    op = TupleOp("write", RelationTuple.of("user:a", "viewer", "agent:b"))
    async with TestSession() as db:
        ids = await enqueue(db, [op], source_table="test", source_id=uuid.uuid4(), now=now)
        await db.commit()
    monkeypatch.setattr(outbox, "IMMEDIATE_TIMEOUT_SECONDS", 0.05)

    with caplog.at_level(logging.WARNING, logger=outbox.__name__):
        # 1 s: far above the 0.05 s budget, below the 2 s default, so a budget bound at import
        # time (instead of read per call) fails here too.
        run = await asyncio.wait_for(apply_now(TestSession, HangingStore(), ids, now=now), 1.0)

    assert run == OutboxRun()
    assert [r.getMessage() for r in caplog.records] == ["authz_outbox_immediate_timeout"]
    async with TestSession() as db:
        row = await db.scalar(select(AuthzOutbox).where(AuthzOutbox.id == ids[0]))
    assert row is not None and row.processed_at is None
