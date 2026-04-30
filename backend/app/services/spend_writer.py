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
  ``(date, target_id)`` per axis and UPSERTed into ``daily_spend_user`` /
  ``daily_spend_agent`` / ``daily_spend_model``.

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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import date as date_type
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.daily_spend_agent import DailySpendAgent
from app.models.daily_spend_model import DailySpendModel
from app.models.daily_spend_user import DailySpendUser

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


# ---------------------------------------------------------------------------
# Internal aggregation helper
# ---------------------------------------------------------------------------


@dataclass
class _Bucket:
    """Pre-flush in-memory aggregator keyed by ``(date, target_id)``."""

    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))
    request_count: int = 0


def _coerce_cost(value: object) -> Decimal:
    """Normalise float/int/Decimal inputs into a ``Decimal``.

    The hook may be called with floats (LangChain usage_metadata) or with
    Decimals (LiteLLM-derived pricing). We always store Decimal so the
    aggregate table stays exact.
    """

    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        # ``str()`` coercion preserves precision; passing the float directly
        # to ``Decimal`` would carry binary-float noise into the DB.
        return Decimal(str(value))
    return Decimal("0")


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
        self._session_factory = async_session

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

        assert self._queue is not None
        while not self._stopping.is_set():
            entries: list[SpendEntry] = []
            try:
                # Wait for at least one entry, bounded by the flush interval.
                first = await asyncio.wait_for(
                    self._queue.get(), timeout=self._flush_interval
                )
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
        """UPSERT a batch into the three aggregate tables."""

        if not entries:
            return

        user_buckets: dict[tuple[date_type, uuid.UUID], _Bucket] = {}
        agent_buckets: dict[tuple[date_type, uuid.UUID], _Bucket] = {}
        model_buckets: dict[tuple[date_type, uuid.UUID], _Bucket] = {}

        for entry in entries:
            cost = _coerce_cost(entry.cost_usd)
            self._accumulate(
                user_buckets, (entry.date, entry.user_id), entry, cost
            )
            if entry.agent_id is not None:
                self._accumulate(
                    agent_buckets, (entry.date, entry.agent_id), entry, cost
                )
            if entry.model_id is not None:
                self._accumulate(
                    model_buckets, (entry.date, entry.model_id), entry, cost
                )

        async with self._session_factory() as db:
            try:
                if user_buckets:
                    await self._upsert_user(db, user_buckets)
                if agent_buckets:
                    await self._upsert_agent(db, agent_buckets)
                if model_buckets:
                    await self._upsert_model(db, model_buckets)
                await db.commit()
            except Exception:  # noqa: BLE001 — keep loop alive
                await db.rollback()
                raise

    @staticmethod
    def _accumulate(
        buckets: dict[tuple[date_type, uuid.UUID], _Bucket],
        key: tuple[date_type, uuid.UUID],
        entry: SpendEntry,
        cost: Decimal,
    ) -> None:
        bucket = buckets.get(key)
        if bucket is None:
            bucket = _Bucket()
            buckets[key] = bucket
        bucket.tokens_in += entry.tokens_in
        bucket.tokens_out += entry.tokens_out
        bucket.cost_usd += cost
        bucket.request_count += entry.request_count

    # -- per-axis upsert helpers --------------------------------------------

    async def _upsert_user(
        self,
        db: AsyncSession,
        buckets: dict[tuple[date_type, uuid.UUID], _Bucket],
    ) -> None:
        await self._upsert(
            db,
            DailySpendUser,
            target_col="user_id",
            unique_cols=("date", "user_id"),
            buckets=buckets,
        )

    async def _upsert_agent(
        self,
        db: AsyncSession,
        buckets: dict[tuple[date_type, uuid.UUID], _Bucket],
    ) -> None:
        await self._upsert(
            db,
            DailySpendAgent,
            target_col="agent_id",
            unique_cols=("date", "agent_id"),
            buckets=buckets,
        )

    async def _upsert_model(
        self,
        db: AsyncSession,
        buckets: dict[tuple[date_type, uuid.UUID], _Bucket],
    ) -> None:
        await self._upsert(
            db,
            DailySpendModel,
            target_col="model_id",
            unique_cols=("date", "model_id"),
            buckets=buckets,
        )

    async def _upsert(
        self,
        db: AsyncSession,
        table_cls: type,
        *,
        target_col: str,
        unique_cols: tuple[str, ...],
        buckets: dict[tuple[date_type, uuid.UUID], _Bucket],
    ) -> None:
        """Dialect-aware UPSERT: PostgreSQL native, SQLite via ON CONFLICT."""

        if not buckets:
            return
        now = datetime.now(UTC).replace(tzinfo=None)
        rows: list[dict[str, Any]] = []
        for (the_date, target_id), bucket in buckets.items():
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "date": the_date,
                    target_col: target_id,
                    "total_tokens_in": bucket.tokens_in,
                    "total_tokens_out": bucket.tokens_out,
                    "total_cost_usd": bucket.cost_usd,
                    "request_count": bucket.request_count,
                    "updated_at": now,
                }
            )

        dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            stmt = pg_insert(table_cls).values(rows)
            update_cols = {
                "total_tokens_in": (
                    table_cls.total_tokens_in + stmt.excluded.total_tokens_in
                ),
                "total_tokens_out": (
                    table_cls.total_tokens_out + stmt.excluded.total_tokens_out
                ),
                "total_cost_usd": (
                    table_cls.total_cost_usd + stmt.excluded.total_cost_usd
                ),
                "request_count": (
                    table_cls.request_count + stmt.excluded.request_count
                ),
                "updated_at": stmt.excluded.updated_at,
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=list(unique_cols),
                set_=update_cols,
            )
            await db.execute(stmt)
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = sqlite_insert(table_cls).values(rows)
            update_cols = {
                "total_tokens_in": (
                    table_cls.total_tokens_in + stmt.excluded.total_tokens_in
                ),
                "total_tokens_out": (
                    table_cls.total_tokens_out + stmt.excluded.total_tokens_out
                ),
                "total_cost_usd": (
                    table_cls.total_cost_usd + stmt.excluded.total_cost_usd
                ),
                "request_count": (
                    table_cls.request_count + stmt.excluded.request_count
                ),
                "updated_at": stmt.excluded.updated_at,
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=list(unique_cols),
                set_=update_cols,
            )
            await db.execute(stmt)
        else:
            # Application-level fallback for any other dialect.
            await self._upsert_application_level(
                db,
                table_cls,
                target_col=target_col,
                unique_cols=unique_cols,
                buckets=buckets,
                now=now,
            )

    async def _upsert_application_level(
        self,
        db: AsyncSession,
        table_cls: type,
        *,
        target_col: str,
        unique_cols: tuple[str, ...],
        buckets: dict[tuple[date_type, uuid.UUID], _Bucket],
        now: datetime,
    ) -> None:
        """Last-resort UPSERT for dialects without native ON CONFLICT support."""

        from sqlalchemy import select

        for (the_date, target_id), bucket in buckets.items():
            existing = await db.execute(
                select(table_cls).where(
                    table_cls.date == the_date,
                    getattr(table_cls, target_col) == target_id,
                )
            )
            row = existing.scalar_one_or_none()
            if row is None:
                row = table_cls(
                    date=the_date,
                    **{target_col: target_id},
                    total_tokens_in=bucket.tokens_in,
                    total_tokens_out=bucket.tokens_out,
                    total_cost_usd=bucket.cost_usd,
                    request_count=bucket.request_count,
                    updated_at=now,
                )
                db.add(row)
            else:
                row.total_tokens_in += bucket.tokens_in
                row.total_tokens_out += bucket.tokens_out
                row.total_cost_usd = (row.total_cost_usd or Decimal("0")) + bucket.cost_usd
                row.request_count += bucket.request_count
                row.updated_at = now


# Module-global singleton — imported by hooks + lifespan.
spend_queue = DailySpendUpdateQueue()


__all__ = ["DailySpendUpdateQueue", "SpendEntry", "spend_queue"]
