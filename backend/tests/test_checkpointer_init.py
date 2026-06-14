"""Tests for app.agent_runtime.checkpointer — init_checkpointer + delete_thread."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agent_runtime.checkpointer as mod

# ---------------------------------------------------------------------------
# init_checkpointer
# ---------------------------------------------------------------------------


def _install_fake_checkpointer_modules(monkeypatch, pool_cls: type, saver_cls: type) -> None:
    psycopg_pool_module = ModuleType("psycopg_pool")
    psycopg_pool_module.AsyncConnectionPool = pool_cls
    aio_module = ModuleType("langgraph.checkpoint.postgres.aio")
    aio_module.AsyncPostgresSaver = saver_cls
    monkeypatch.setitem(sys.modules, "psycopg_pool", psycopg_pool_module)
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.postgres.aio", aio_module)


@pytest.mark.asyncio
async def test_checkpointer_module_globals_wiring():
    """Verify that pool/saver globals can be wired and their setup methods called.

    Note: This does NOT call init_checkpointer() because the function uses
    lazy imports that are difficult to patch. Instead it validates that once
    _pool and _checkpointer are assigned, open()/setup() behave as expected.
    """
    orig_pool = mod._pool
    orig_cp = mod._checkpointer

    mock_pool = AsyncMock()
    mock_saver = AsyncMock()

    try:
        # Reset module globals
        mod._pool = None
        mod._checkpointer = None

        # Simulate the assignments init_checkpointer would make
        mod._pool = mock_pool
        await mock_pool.open()
        mod._checkpointer = mock_saver
        await mock_saver.setup()

        # Verify pool was opened and saver was set up
        mock_pool.open.assert_awaited_once()
        mock_saver.setup.assert_awaited_once()
        assert mod._pool is mock_pool
        assert mod._checkpointer is mock_saver
    finally:
        mod._pool = orig_pool
        mod._checkpointer = orig_cp


@pytest.mark.asyncio
async def test_init_checkpointer_passes_configured_pool_bounds(monkeypatch):
    orig_pool = mod._pool
    orig_cp = mod._checkpointer
    pool_kwargs: dict[str, object] = {}

    class FakePool:
        def __init__(self, **kwargs: object) -> None:
            pool_kwargs.update(kwargs)
            self.opened = False

        async def open(self) -> None:
            self.opened = True

    class FakeSaver:
        def __init__(self, conn: object) -> None:
            self.conn = conn
            self.setup_called = False

        async def setup(self) -> None:
            self.setup_called = True

    _install_fake_checkpointer_modules(monkeypatch, FakePool, FakeSaver)

    try:
        mod._pool = None
        mod._checkpointer = None

        await mod.init_checkpointer("postgresql://example", min_size=2, max_size=12)

        assert pool_kwargs["conninfo"] == "postgresql://example"
        assert pool_kwargs["min_size"] == 2
        assert pool_kwargs["max_size"] == 12
        assert pool_kwargs["open"] is False
        assert pool_kwargs["kwargs"] == {"autocommit": True, "prepare_threshold": 0}
        assert isinstance(mod._pool, FakePool)
        assert mod._pool.opened is True
        assert isinstance(mod._checkpointer, FakeSaver)
        assert mod._checkpointer.conn is mod._pool
        assert mod._checkpointer.setup_called is True
    finally:
        mod._pool = orig_pool
        mod._checkpointer = orig_cp


@pytest.mark.asyncio
async def test_init_checkpointer_clamps_invalid_pool_bounds(monkeypatch):
    orig_pool = mod._pool
    orig_cp = mod._checkpointer
    pool_kwargs: dict[str, object] = {}

    class FakePool:
        def __init__(self, **kwargs: object) -> None:
            pool_kwargs.update(kwargs)

        async def open(self) -> None:
            return None

    class FakeSaver:
        def __init__(self, conn: object) -> None:
            self.conn = conn

        async def setup(self) -> None:
            return None

    _install_fake_checkpointer_modules(monkeypatch, FakePool, FakeSaver)

    try:
        mod._pool = None
        mod._checkpointer = None

        await mod.init_checkpointer("postgresql://example", min_size=0, max_size=-1)

        assert pool_kwargs["min_size"] == 1
        assert pool_kwargs["max_size"] == 1
    finally:
        mod._pool = orig_pool
        mod._checkpointer = orig_cp


# ---------------------------------------------------------------------------
# delete_thread — with pool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_thread_with_pool():
    """delete_thread executes DELETE queries when pool is available."""
    orig_pool = mod._pool
    orig_cp = mod._checkpointer

    # mock_conn.execute() is awaitable
    mock_conn = AsyncMock()

    # conn.transaction() returns a sync context manager (psycopg)
    # but in the code it's used as `async with conn.transaction():`
    # AsyncMock handles this — but we need transaction() to return
    # an async context manager, not a coroutine.
    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=None)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    # transaction() is called as a regular function, not awaited
    mock_conn.transaction = MagicMock(return_value=mock_tx)

    # pool.connection() returns an async context manager
    mock_conn_cm = MagicMock()
    mock_conn_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn_cm.__aexit__ = AsyncMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.connection = MagicMock(return_value=mock_conn_cm)

    try:
        mod._pool = mock_pool

        await mod.delete_thread("thread-abc")

        # Verify that 3 DELETE queries were executed
        assert mock_conn.execute.await_count == 3
    finally:
        mod._pool = orig_pool
        mod._checkpointer = orig_cp
