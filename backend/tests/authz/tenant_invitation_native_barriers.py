"""PostgreSQL lock-acquisition barriers for company invitation HTTP races."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

import anyio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

P = ParamSpec("P")
R = TypeVar("R")


class NativeLockBarrier:
    """Pause the first request after its real lock and observe the blocked peer."""

    def __init__(self) -> None:
        self.first_acquired = anyio.Event()
        self.second_started = anyio.Event()
        self.second_acquired = anyio.Event()
        self.release_first = anyio.Event()
        self._guard = anyio.Lock()
        self._pids: list[int] = []

    async def run(
        self,
        db: AsyncSession,
        operation: Callable[P, Awaitable[R]],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        pid = await db.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)
        async with self._guard:
            position = len(self._pids)
            self._pids.append(pid)
            if position == 1:
                self.second_started.set()
        result = await operation(*args, **kwargs)
        if position == 0:
            self.first_acquired.set()
            await self.release_first.wait()
        else:
            self.second_acquired.set()
        return result

    async def prove_second_blocked(self, observer: async_sessionmaker[AsyncSession]) -> None:
        await self.second_started.wait()
        first_pid, second_pid = self._pids
        statement = text(
            "SELECT :first_pid = ANY(pg_blocking_pids(:second_pid)) "
            "AND wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :second_pid"
        )
        with anyio.fail_after(10):
            while True:
                async with observer() as db:
                    blocked = await db.scalar(
                        statement,
                        {"first_pid": first_pid, "second_pid": second_pid},
                    )
                if blocked is True:
                    return
                await anyio.sleep(0.05)

    def release(self) -> None:
        self.release_first.set()
