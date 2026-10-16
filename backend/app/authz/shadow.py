"""Shadow-mode recorder: re-decide legacy answers with OpenFGA off the request path (8.5).

``ShadowRecorder.submit`` is the ``AuthzService`` shadow hook. It never awaits:
samples over ``AUTHZ_SHADOW_MAX_RPS`` or beyond the bounded queue are dropped and
counted. One background worker drains batches, asks ``shadow_decide`` (bounded
concurrency, per-decision timeout) and upserts disagreements into
``authz_shadow_diffs`` (one row per key, ``count`` aggregated). OpenFGA or database
failures are counted and logged, never raised (8.5). Samples vetoed only by a
pending revocation marker (C-08) are excluded from diffs. A worker that dies is
logged and closes the recorder; it is not restarted.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.config import AuthzSettings
from app.authz.service_types import ShadowDecision, ShadowSample
from app.authz.shadow_metrics import (
    RateLimit,
    ShadowCounters,
    bind_shadow_request,
    current_request,
)
from app.authz.shadow_store import DiffKey, DiffTally, diff_key, diff_sessions, upsert_diffs

__all__ = ["RateLimit", "ShadowCounters", "ShadowRecorder", "bind_shadow_request"]

logger = logging.getLogger(__name__)

Decide = Callable[[ShadowSample], Awaitable[ShadowDecision]]


@dataclass(frozen=True, slots=True)
class _Item:
    sample: ShadowSample
    route: str
    request_id: str | None


class ShadowRecorder:
    def __init__(
        self,
        decide: Decide,
        *,
        max_rps: int,
        sessions: Callable[[], AsyncSession] | None = None,
        queue_size: int = 1000,
        batch_size: int = 100,
        concurrency: int = 8,
        decision_seconds: float = 2.0,
        summary_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._decide = decide
        self._sessions = sessions or diff_sessions()
        self._limit = RateLimit(max_rps, clock)
        self._queue: asyncio.Queue[_Item] = asyncio.Queue(maxsize=queue_size)
        self._batch_size = batch_size
        self._slots = asyncio.Semaphore(concurrency)
        self._decision_seconds = decision_seconds
        self._summary_seconds = summary_seconds
        self._tasks: list[asyncio.Task[None]] = []
        self._closed = False
        self._counters = ShadowCounters()

    def counters(self) -> ShadowCounters:
        """A snapshot for health/metrics readers."""
        return self._counters.snapshot()

    def submit(self, sample: ShadowSample) -> None:
        """The request-path hook: O(1), never awaits, never raises on overload."""
        self._counters.submitted += 1
        if self._closed:
            self._counters.dropped += 1
            return
        if not self._limit.admit():
            self._counters.rate_limited += 1
            return
        route, request_id = current_request()
        try:
            self._queue.put_nowait(_Item(sample, route, request_id))
        except asyncio.QueueFull:
            self._counters.dropped += 1

    def start(self) -> None:
        if self._tasks:
            return
        worker = asyncio.create_task(self._run(), name="authz-shadow-recorder")
        worker.add_done_callback(self._worker_done)
        summary = asyncio.create_task(self._summaries(), name="authz-shadow-summary")
        self._tasks = [worker, summary]

    async def stop(self, drain_seconds: float = 5.0) -> None:
        """Refuse new samples, drain the queue for up to ``drain_seconds``, then cancel."""
        self._closed = True
        if not self._tasks:
            return
        if not self._tasks[0].done():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._queue.join(), drain_seconds)
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self._counters.dropped += self._queue.qsize()
        self._log_summary()

    def _worker_done(self, task: asyncio.Task[None]) -> None:
        if not self._closed:
            error = None if task.cancelled() else task.exception()
            logger.error("authz shadow worker stopped; recorder closed", exc_info=error)
        self._closed = True

    async def _summaries(self) -> None:
        while True:
            await asyncio.sleep(self._summary_seconds)
            self._log_summary()

    def _log_summary(self) -> None:
        logger.info("authz_shadow_summary", extra=self._counters.summary())

    async def _run(self) -> None:
        while True:
            batch = [await self._queue.get()]
            while len(batch) < self._batch_size and not self._queue.empty():
                batch.append(self._queue.get_nowait())
            try:
                await self._process(batch)
            except asyncio.CancelledError:
                self._counters.dropped += len(batch)  # in flight when stop gave up
                raise
            except Exception:
                self._counters.write_errors += 1
                logger.warning("authz shadow diff write failed", exc_info=True)
            finally:
                for _ in batch:
                    self._queue.task_done()

    async def _process(self, batch: list[_Item]) -> None:
        keys = await asyncio.gather(*(self._compare(item) for item in batch))
        tallies: dict[DiffKey, DiffTally] = {}
        for item, key in zip(batch, keys, strict=True):
            if key is None:
                continue
            tally = tallies.setdefault(key, DiffTally(0, item.request_id))
            tally.count += 1
            tally.request_id = item.request_id or tally.request_id
        if tallies:
            async with self._sessions() as db:
                await upsert_diffs(db, tallies)
                await db.commit()

    async def _compare(self, item: _Item) -> DiffKey | None:
        sample = item.sample
        try:
            async with self._slots:
                decision = await asyncio.wait_for(self._decide(sample), self._decision_seconds)
        except Exception:  # OpenFGA outage or timeout (8.5: record, ignore)
            self._counters.errors += 1
            logger.warning("authz shadow decision failed", exc_info=True)
            return None
        self._counters.evaluated += 1
        if decision.overlay_vetoed:
            self._counters.overlay_vetoed += 1
            return None
        key = diff_key(sample, decision.verdict, item.route)
        if key is None:
            self._counters.matched += 1
            return None
        resource_type = sample.obj.partition(":")[0]
        self._counters.mismatches += 1
        self._counters.mismatch_by_type[(sample.principal.org_id, resource_type)] += 1
        logger.warning(
            "authz_shadow_mismatch",
            extra={
                "org_id": str(sample.principal.org_id),
                "resource_type": resource_type,
                "permission": key.permission,
                "legacy": key.legacy,
                "fga": key.fga,
                "route": key.route,
            },
        )
        return key


_recorder: ShadowRecorder | None = None


def start_shadow_recorder(
    settings: AuthzSettings,
    decide: Decide,
    *,
    sessions: Callable[[], AsyncSession] | None = None,
) -> ShadowRecorder | None:
    """Lifespan entry: a running recorder only when ``AUTHZ_MODE=shadow``."""
    global _recorder
    if settings.authz_mode != "shadow":
        return None
    if _recorder is None:
        _recorder = ShadowRecorder(decide, max_rps=settings.authz_shadow_max_rps, sessions=sessions)
        _recorder.start()
    return _recorder


async def stop_shadow_recorder(drain_seconds: float = 5.0) -> None:
    """Lifespan exit; a no-op when the recorder never started."""
    global _recorder
    recorder, _recorder = _recorder, None
    if recorder is not None:
        await recorder.stop(drain_seconds)
