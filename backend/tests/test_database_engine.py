from __future__ import annotations

import anyio

from app import database


def test_database_engine_pre_pings_pooled_connections() -> None:
    assert database.engine.sync_engine.pool._pre_ping is True


def test_session_factory_uses_shielded_session_class() -> None:
    assert database.async_session.class_ is database.ShieldedAsyncSession


async def test_close_session_shielded_finishes_under_outer_cancellation(
    monkeypatch,
) -> None:
    session = database.async_session()
    closed = False

    async def close() -> None:
        nonlocal closed
        await anyio.lowlevel.checkpoint()
        closed = True

    monkeypatch.setattr(session, "close", close)

    with anyio.CancelScope() as scope:
        scope.cancel()
        await database.close_session_shielded(session)

    assert closed is True
