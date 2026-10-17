"""Daily spend update queue + batch UPSERT writer.

Pattern borrowed from prior art — see ``NOTICES.md`` for the LiteLLM
``db_spend_update_writer`` reference. Identifiers, dataclass shape, and
on-conflict logic are Moldy-native; we do not import or copy any external
code.

Responsibilities
----------------
* :class:`SpendEntry` — the dataclass enqueued by :class:`SpendHook` after a
  successful agent call (one entry per call).
* :class:`DailySpendUpdateQueue` — an in-memory ``asyncio.Queue`` drained by
  a background task on ``flush_interval_sec`` ticks **or** when the buffered
  count reaches ``batch_size``. Drained entries are grouped by
  ``(date, org_id, target_id)`` per axis and UPSERTed into ``daily_spend_user`` /
  ``daily_spend_agent`` / ``daily_spend_model``. Every entry carries its company
  (``tenant_id``, required); a batch is flushed one company per session (H-07, 8.8).

Concurrency invariants
----------------------
* ``add()`` is non-blocking (uses ``put_nowait``). Producer-side failures —
  full queue, missing event loop — are swallowed with a warning so a hook
  call never fails the agent invocation.
* Exactly one background drain task per process (started in lifespan).
* ``stop()`` is graceful: drains remaining entries, then cancels the loop.
* The flush is dialect-aware: PostgreSQL uses native ``ON CONFLICT ... DO
  UPDATE``; SQLite ≥ 3.24 honours the same syntax. Both drivers map cleanly
  through SQLAlchemy's ``insert()`` with ``on_conflict_do_update`` /
  ``on_conflict_do_update`` polyfill.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date as date_type
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.spend_flush import flush_by_tenant

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SpendEntry
# ---------------------------------------------------------------------------


@dataclass
class SpendEntry:
    """Single accounting event produced by :class:`SpendHook`.

    ``user_id`` is required (every aggregate row has one). ``agent_id`` and
    ``model_id`` are optional because some surfaces (model preview test) have
    no agent context. Missing target ids simply skip that axis at flush time.
    """

    date: date_type
    user_id: uuid.UUID
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    agent_id: uuid.UUID | None = None
    model_id: uuid.UUID | None = None
    request_count: int = 1
    # F-14 (6.2, 8.7): the run's organization/company; aggregates are keyed by org.
    org_id: uuid.UUID | None = None
    # H-07 (8.8): queue items always name their company; flushes never mix companies.
    tenant_id: uuid.UUID = field(kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, uuid.UUID):
            raise TypeError("spend entries require a tenant_id")


# ---------------------------------------------------------------------------
# DailySpendUpdateQueue
# ---------------------------------------------------------------------------


class DailySpendUpdateQueue:
    """In-process buffer + background drainer for spend aggregates.

    Lifecycle is owned by the FastAPI app (``main.py`` lifespan). One queue
    handles all three axes — splitting by axis would force the producer to
    enqueue three times per call.
    """

    def __init__(
        self,
        flush_interval_sec: float = 5.0,
        batch_size: int = 100,
        max_queue_size: int = 10_000,
    ) -> None:
        self._flush_interval = flush_interval_sec
        self._batch_size = batch_size
        self._max_queue_size = max_queue_size
        # ``Queue`` is created lazily so module import doesn't bind to a loop.
        self._queue: asyncio.Queue[SpendEntry] | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        # Tests mock this with their own ``async_sessionmaker``.
        self._session_factory: Callable[[], AsyncSession] | None = None

    # -- producer side -------------------------------------------------------

    def add(self, entry: SpendEntry) -> None:
        """Enqueue an entry without awaiting.

        Producer failures are intentionally non-fatal — accounting must never
        crash an agent run. Common reasons we degrade to a warning:

        * The drain task hasn't started yet (before lifespan completes).
        * The queue is saturated — ``put_nowait`` raises ``QueueFull``.
        * No running event loop (synchronous test fixture).
        """

        try:
            queue = self._ensure_queue()
        except RuntimeError:
            logger.warning("spend queue add() called outside running event loop; dropped entry")
            return
        try:
            queue.put_nowait(entry)
        except asyncio.QueueFull:
            logger.warning(
                "spend queue is full (max=%d); dropping entry to protect runtime",
                self._max_queue_size,
            )

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Begin the background drain loop. Idempotent."""

        if self._task is not None and not self._task.done():
            return
        self._stopping.clear()
        self._ensure_queue()
        self._task = asyncio.create_task(self._loop(), name="spend_writer_loop")
        logger.info(
            "spend writer started (flush_interval=%.1fs batch=%d)",
            self._flush_interval,
            self._batch_size,
        )

    async def stop(self) -> None:
        """Drain remaining entries, then cancel the background task."""

        self._stopping.set()
        try:
            await self._flush_remaining()
        except Exception:  # noqa: BLE001 — shutdown path
            logger.warning("final spend flush raised", exc_info=True)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        logger.info("spend writer stopped")

    # -- private -------------------------------------------------------------

    def _ensure_queue(self) -> asyncio.Queue[SpendEntry]:
        if self._queue is None:
            # Touching ``get_running_loop`` here surfaces a useful error when a
            # caller tries to enqueue before lifespan has started.
            asyncio.get_running_loop()
            self._queue = asyncio.Queue(maxsize=self._max_queue_size)
        return self._queue

    async def _loop(self) -> None:
        """Drain the queue in batches until ``stop`` is requested."""

        assert self._queue is not None  # noqa: S101 — start() creates the queue before _loop (type narrowing)
        while not self._stopping.is_set():
            entries: list[SpendEntry] = []
            try:
                # Wait for at least one entry, bounded by the flush interval.
                first = await asyncio.wait_for(self._queue.get(), timeout=self._flush_interval)
                entries.append(first)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            # Greedy drain up to batch_size — short-circuits the next sleep.
            while len(entries) < self._batch_size:
                try:
                    entries.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            try:
                await self._flush_batch(entries)
            except Exception:  # noqa: BLE001 — preserve the loop
                logger.warning("spend batch flush raised", exc_info=True)

    async def _flush_remaining(self) -> None:
        """Drain everything currently buffered into a single flush."""

        if self._queue is None:
            return
        entries: list[SpendEntry] = []
        while True:
            try:
                entries.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if entries:
            await self._flush_batch(entries)

    async def _flush_batch(self, entries: list[SpendEntry]) -> None:
        """UPSERT a batch into the three aggregate tables, one company at a time."""

        if entries:
            await flush_by_tenant(entries, self._session_factory)


# Module-global singleton — imported by hooks + lifespan.
spend_queue = DailySpendUpdateQueue()


__all__ = ["DailySpendUpdateQueue", "SpendEntry", "spend_queue"]
