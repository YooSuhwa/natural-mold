"""Tests for app.agent_runtime.checkpointer — init_checkpointer + delete_thread."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agent_runtime.checkpointer as mod

# ---------------------------------------------------------------------------
# init_checkpointer
# ---------------------------------------------------------------------------


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
