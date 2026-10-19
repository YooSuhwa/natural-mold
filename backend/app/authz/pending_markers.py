"""Process-wide hint of unfinalized revocation markers (plan 8.6 step 4).

The process re-reads the instance-wide pending count at most once per ``ttl``
(1 second); while it is zero the deny overlay skips the marker table. A marker
recorded in this process forces table lookups for the next ``ttl`` without
waiting for a re-read, so this process never trusts a stale zero after its own
revocation. Other processes see a new marker within ``ttl`` (the bounded window
the plan accepts). A failed read is never cached and propagates (fail closed).
"""

from collections.abc import Awaitable, Callable
from time import monotonic


class PendingMarkers:
    def __init__(self, *, ttl: float = 1.0, clock: Callable[[], float] = monotonic) -> None:
        self.ttl = ttl
        self._clock = clock
        self._count: int | None = None
        self._read_at = 0.0

    def _fresh(self) -> bool:
        return self._count is not None and self._clock() - self._read_at < self.ttl

    def note_recorded(self) -> None:
        """Count our own marker as pending for a full ``ttl`` from now."""
        self._count = max(self._count or 0, 1)
        self._read_at = self._clock()

    def reset(self) -> None:
        self._count = None
        self._read_at = 0.0

    async def any_pending(self, read_count: Callable[[], Awaitable[int]]) -> bool:
        if not self._fresh():
            count = await read_count()
            self._count, self._read_at = count, self._clock()
        return bool(self._count)


pending_markers = PendingMarkers()
