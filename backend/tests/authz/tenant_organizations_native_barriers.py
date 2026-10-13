"""Observable PostgreSQL lock barriers for Task 7 native HTTP races."""

from dataclasses import dataclass, field

import anyio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(slots=True)
class NativeLockBarrier:
    first_acquired: anyio.Event = field(default_factory=anyio.Event)
    second_started: anyio.Event = field(default_factory=anyio.Event)
    release_event: anyio.Event = field(default_factory=anyio.Event)
    _sequence: anyio.Lock = field(default_factory=anyio.Lock)
    _attempts: int = 0
    first_pid: int | None = None
    second_pid: int | None = None

    async def before_attempt(self, db: AsyncSession) -> int:
        pid = await db.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(pid, int)
        async with self._sequence:
            self._attempts += 1
            ordinal = self._attempts
            if ordinal == 1:
                self.first_pid = pid
            elif ordinal == 2:
                self.second_pid = pid
                self.second_started.set()
            else:
                raise AssertionError("barrier supports exactly two writers")
        return ordinal

    async def after_actual_lock(self, ordinal: int) -> None:
        if ordinal == 1:
            self.first_acquired.set()
            await self.release_event.wait()

    async def wait_for_second_blocked(
        self, observer: async_sessionmaker[AsyncSession]
    ) -> tuple[int, int]:
        with anyio.fail_after(5):
            await self.second_started.wait()
            assert self.second_pid is not None
            while True:
                async with observer() as db:
                    blockers = await db.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": self.second_pid}
                    )
                    waiting = await db.scalar(
                        text(
                            "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"
                        ),
                        {"pid": self.second_pid},
                    )
                if blockers and self.first_pid in blockers and waiting is True:
                    assert self.first_pid is not None
                    return self.first_pid, self.second_pid
                await anyio.sleep(0.01)
        raise AssertionError("second writer did not block on the first writer")

    def release(self) -> None:
        self.release_event.set()
