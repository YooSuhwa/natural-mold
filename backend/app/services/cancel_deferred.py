"""Let a cancel land between database statements, never inside one.

A ``task.cancel()`` that arrives while a session is mid-statement makes SQLAlchemy
invalidate the pooled connection (the driver call is torn, the connection is thrown
away and replaced). Loops that are routinely stopped with ``cancel()`` — run
heartbeats — run each database step through :func:`finish_then_cancel` so a stop
waits for the step in flight and then takes effect.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine
from typing import Any


async def finish_then_cancel[T](step: Coroutine[Any, Any, T]) -> T:
    """Await ``step``; a cancel received meanwhile is re-raised once the step is done."""
    task = asyncio.ensure_future(step)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait({task})
        if not task.cancelled():
            task.exception()  # the cancel wins; mark a step failure as retrieved
        raise
