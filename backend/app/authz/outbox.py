"""Transactional OpenFGA projection queue (plan 6.1 ``authz_outbox``, 8.6 items 1, 2 and 6).

Writers enqueue rows in the caller's transaction (``enqueue``); after commit the request
applies them at once (``apply_now``, best effort, 2 seconds) and the C-05 leader job drains
the rest (``process_outbox``). Every OpenFGA call is idempotent (duplicate writes and missing
deletes ignored), so a row applied twice is harmless. Rows apply in INSERT order (``seq``,
assigned while the writer holds its object locks; never a clock): a row waits while an
older row of its tuple key is still pending, and a failed row blocks the later rows of its
key in the same batch. A ``replace`` write deletes the key first
because OpenFGA cannot change a stored tuple's condition in place.
"""

import logging
from collections.abc import Callable, Collection, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID, uuid4

import anyio
from pydantic import JsonValue
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.grant_completion import complete_sources
from app.authz.tuple_types import RelationTuple
from app.models.authz import AuthzOutbox

logger = logging.getLogger(__name__)
MAX_BATCH = 100
ALERT_ATTEMPTS = 10
IMMEDIATE_TIMEOUT_SECONDS = 2.0
_MAX_BACKOFF = timedelta(minutes=5)
_ERROR_WIDTH = 500
Sessions = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class TupleWriter(Protocol):
    async def write(self, tuples: Sequence[RelationTuple]) -> None: ...

    async def delete(self, tuples: Sequence[RelationTuple]) -> None: ...


@dataclass(frozen=True, slots=True)
class TupleOp:
    op: Literal["write", "delete"]
    tuple: RelationTuple
    replace: bool = False


@dataclass(frozen=True, slots=True)
class OutboxRun:
    processed: tuple[UUID, ...] = ()
    failed: tuple[UUID, ...] = ()
    deferred: tuple[UUID, ...] = ()


def encode(op: TupleOp) -> dict[str, JsonValue]:
    item = op.tuple
    data: dict[str, JsonValue] = {"user": item.user, "relation": item.relation}
    data["object"] = item.object
    if item.condition is not None:
        data["condition"] = {"name": item.condition.name, "context": item.condition.context}
    if op.replace:
        data["replace"] = True
    return data


def decode(op: str, data: dict[str, JsonValue]) -> TupleOp:
    if op not in ("write", "delete"):
        raise ValueError(f"unknown outbox op {op!r}")
    condition = data.get("condition")
    name = context = None
    if isinstance(condition, dict):
        name, raw = str(condition["name"]), condition.get("context")
        context = raw if isinstance(raw, dict) else {}
    item = RelationTuple.of(
        str(data["user"]), str(data["relation"]), str(data["object"]), name, context
    )
    return TupleOp(op, item, replace=data.get("replace") is True)


def tuple_key(item: RelationTuple) -> str:
    return f"{item.user} {item.relation} {item.object}"


async def enqueue(
    db: AsyncSession, ops: Sequence[TupleOp], *, source_table: str, source_id: UUID, now: datetime
) -> list[UUID]:
    """Add queue rows to the caller's transaction (flushed, never committed here).

    One INSERT per row, so ``seq`` follows ``ops`` order on every dialect. No RETURNING:
    request scopes may insert queue rows under RLS but only the platform drain reads them.
    """
    await db.flush()  # pending ORM rows first, so their tuples keep their place in the queue
    ids: list[UUID] = []
    for op in ops:
        row_id = uuid4()
        await db.execute(
            insert(AuthzOutbox).values(
                id=row_id,
                op=op.op,
                tuple=encode(op),
                tuple_key=tuple_key(op.tuple),
                source_table=source_table,
                source_id=source_id,
                attempts=0,
                next_attempt_at=now,
            )
        )
        ids.append(row_id)
    return ids


async def process_outbox(
    sessions: Sessions,
    writer: TupleWriter,
    *,
    ids: Collection[UUID] | None = None,
    limit: int = MAX_BATCH,
    now: datetime | None = None,
) -> OutboxRun:
    """Apply one batch of due rows, then close revocations whose deletes all landed."""
    stamp = now or datetime.now(UTC)
    async with sessions() as db:
        batch = await _due(db, ids, limit, stamp)
        if not batch:
            return OutboxRun()
        run = await _apply(writer, batch, await _older_pending(db, batch), stamp)
        done = {(row.source_table, row.source_id) for row in batch if row.id in run.processed}
        try:  # a completion bug must not lose the applied rows; markers stay pending
            async with db.begin_nested():
                await complete_sources(db, done, now=stamp)
        except Exception:
            logger.exception("authz_outbox_completion_failed")
        await db.commit()
    return run


async def apply_now(
    sessions: Sessions,
    writer: TupleWriter,
    ids: Collection[UUID],
    *,
    budget_seconds: float = IMMEDIATE_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> OutboxRun:
    """8.6 item 2: right after commit; any failure leaves the rows to the worker."""
    if not ids:
        return OutboxRun()
    try:
        with anyio.move_on_after(budget_seconds):
            return await process_outbox(sessions, writer, ids=ids, limit=len(ids), now=now)
        logger.warning("authz_outbox_immediate_timeout", extra={"rows": len(ids)})
    except Exception as error:  # CancelledError is a BaseException and propagates
        logger.warning("authz_outbox_immediate_failed", extra={"error": type(error).__name__})
    return OutboxRun()


async def _due(
    db: AsyncSession, ids: Collection[UUID] | None, limit: int, stamp: datetime
) -> list[AuthzOutbox]:
    query = select(AuthzOutbox).where(
        AuthzOutbox.processed_at.is_(None), AuthzOutbox.next_attempt_at <= stamp
    )
    if ids is not None:
        query = query.where(AuthzOutbox.id.in_(list(ids)))
    query = query.order_by(AuthzOutbox.seq).limit(limit)
    return list(await db.scalars(query.with_for_update(skip_locked=True)))


async def _older_pending(db: AsyncSession, batch: list[AuthzOutbox]) -> dict[str, int]:
    """Lowest ``seq`` of an unprocessed row outside the batch, per key of a batch row."""
    found = await db.execute(
        select(AuthzOutbox.tuple_key, func.min(AuthzOutbox.seq))
        .where(
            AuthzOutbox.processed_at.is_(None),
            AuthzOutbox.tuple_key.in_(sorted({row.tuple_key for row in batch})),
            AuthzOutbox.id.not_in([row.id for row in batch]),
        )
        .group_by(AuthzOutbox.tuple_key)
    )
    return {key: int(lowest) for key, lowest in found.all()}


async def _apply(
    writer: TupleWriter,
    batch: list[AuthzOutbox],
    older: dict[str, int],
    stamp: datetime,
) -> OutboxRun:
    processed: list[UUID] = []
    failed: list[UUID] = []
    deferred: list[UUID] = []
    blocked: set[str] = set()
    for row in batch:
        key = row.tuple_key
        if key in blocked or older.get(key, row.seq) < row.seq:
            deferred.append(row.id)
            continue
        try:
            await _send(writer, decode(row.op, row.tuple))
        except Exception as error:  # any failure: back off this row, hold its key
            _back_off(row, error, stamp)
            failed.append(row.id)
            blocked.add(key)
            continue
        row.processed_at = stamp
        row.last_error = None
        processed.append(row.id)
    return OutboxRun(tuple(processed), tuple(failed), tuple(deferred))


async def _send(writer: TupleWriter, op: TupleOp) -> None:
    if op.op == "delete" or op.replace:
        await writer.delete([op.tuple])
    if op.op == "write":
        await writer.write([op.tuple])


def _back_off(row: AuthzOutbox, error: Exception, stamp: datetime) -> None:
    row.attempts = (row.attempts or 0) + 1
    row.next_attempt_at = stamp + min(timedelta(seconds=2**row.attempts), _MAX_BACKOFF)
    row.last_error = f"{type(error).__name__}: {error}"[:_ERROR_WIDTH]
    if row.attempts % ALERT_ATTEMPTS == 0:  # 8.6 item 6: alert at 10, 20, ... (C-05/S27)
        logger.error(
            "authz_outbox_failed", extra={"outbox_id": str(row.id), "attempts": row.attempts}
        )
