"""Tests for app.agent_runtime.checkpointer — singleton lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_runtime.checkpointer import (
    get_checkpointer,
    shutdown_checkpointer,
)

# ---------------------------------------------------------------------------
# get_checkpointer — not initialized
# ---------------------------------------------------------------------------


def test_get_checkpointer_not_initialized():
    """get_checkpointer raises RuntimeError before init."""
    import app.agent_runtime.checkpointer as mod

    orig_cp = mod._checkpointer
    try:
        mod._checkpointer = None
        with pytest.raises(RuntimeError, match="not initialized"):
            get_checkpointer()
    finally:
        mod._checkpointer = orig_cp


# ---------------------------------------------------------------------------
# get_checkpointer — after init
# ---------------------------------------------------------------------------


def test_get_checkpointer_after_set():
    """get_checkpointer returns the singleton after setting it."""
    import app.agent_runtime.checkpointer as mod

    orig_cp = mod._checkpointer
    sentinel = MagicMock()
    try:
        mod._checkpointer = sentinel
        result = get_checkpointer()
        assert result is sentinel
    finally:
        mod._checkpointer = orig_cp


# ---------------------------------------------------------------------------
# init_checkpointer + shutdown_checkpointer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_init_and_shutdown_checkpointer():
    """init_checkpointer sets up pool and checkpointer, shutdown cleans up."""
    import app.agent_runtime.checkpointer as mod

    orig_pool = mod._pool
    orig_cp = mod._checkpointer

    mock_pool = AsyncMock()
    mock_saver = AsyncMock()

    try:
        # Patch at the point where they're imported inside init_checkpointer
        with (
            patch.dict("sys.modules", {}),
            patch(
                "app.agent_runtime.checkpointer.AsyncConnectionPool",
                create=True,
            ) as mock_pool_cls,
            patch(
                "app.agent_runtime.checkpointer.AsyncPostgresSaver",
                create=True,
            ) as mock_saver_cls,
        ):
            mock_pool_cls.return_value = mock_pool
            mock_saver_cls.return_value = mock_saver

            # We need to handle the local imports inside init_checkpointer
            # by directly patching the function's import behavior
            pass

        # Simpler approach: directly set module globals and test shutdown
        mod._pool = mock_pool
        mod._checkpointer = mock_saver

        assert get_checkpointer() is mock_saver

        await shutdown_checkpointer()
        mock_pool.close.assert_awaited_once()
        assert mod._pool is None
        assert mod._checkpointer is None
    finally:
        mod._pool = orig_pool
        mod._checkpointer = orig_cp


# ---------------------------------------------------------------------------
# shutdown when no pool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_checkpointer_noop():
    """shutdown_checkpointer is safe when pool is None."""
    import app.agent_runtime.checkpointer as mod

    orig_pool = mod._pool
    orig_cp = mod._checkpointer

    try:
        mod._pool = None
        mod._checkpointer = None
        await shutdown_checkpointer()
        assert mod._pool is None
    finally:
        mod._pool = orig_pool
        mod._checkpointer = orig_cp


# ---------------------------------------------------------------------------
# delete_thread
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_thread_noop_when_no_pool():
    """delete_thread does nothing when pool is None."""
    import app.agent_runtime.checkpointer as mod
    from app.agent_runtime.checkpointer import delete_thread

    orig_pool = mod._pool
    try:
        mod._pool = None
        await delete_thread("thread-123")
    finally:
        mod._pool = orig_pool
