"""Failed outbox rows for the operator sync screen and their retry (14.3, S27; H-04).

A failure is an unprocessed outbox row with at least one failed attempt. The view names
the company (read from the source row's ``tenant_id`` when its table has one), the
resource type and id from the tuple object, and the exception class of the last error;
never the error text, the tuple's subject or a resource name (S27 "상태").

Retry makes the row due now; the next C-05 drain tick applies it in ``seq`` order, so a
manual retry never jumps ahead of an older row of the same key.
"""

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base
from app.models.authz import AuthzOutbox
from app.schemas.admin_authz import OutboxFailure

MAX_LIMIT = 200


def _object(row: AuthzOutbox) -> tuple[str, str]:
    raw = row.tuple.get("object") if isinstance(row.tuple, dict) else None
    kind, _, ident = str(raw or "").partition(":")
    return kind or "unknown", ident


def _error_type(row: AuthzOutbox) -> str | None:
    if not row.last_error:
        return None
    return row.last_error.split(":", 1)[0][:80]


async def _tenants(db: AsyncSession, rows: list[AuthzOutbox]) -> dict[UUID, UUID]:
    """``source_id`` → ``tenant_id`` for sources whose table has a ``tenant_id``."""
    by_table: dict[str, set[UUID]] = defaultdict(set)
    for row in rows:
        by_table[row.source_table].add(row.source_id)
    found: dict[UUID, UUID] = {}
    for name, ids in by_table.items():
        table = Base.metadata.tables.get(name)
        if name == "tenants":
            found.update({ident: ident for ident in ids})
        elif table is not None and "tenant_id" in table.c and "id" in table.c:
            query = select(table.c.id, table.c.tenant_id).where(table.c.id.in_(ids))
            found.update(dict((await db.execute(query)).tuples().all()))
    return found


def _view(row: AuthzOutbox, tenant_id: UUID | None) -> OutboxFailure:
    kind, ident = _object(row)
    return OutboxFailure(
        id=row.id,
        tenant_id=tenant_id,
        resource_type=kind,
        resource_id=ident,
        op=row.op,
        attempts=row.attempts,
        error_type=_error_type(row),
        created_at=row.created_at,
        next_attempt_at=row.next_attempt_at,
    )


async def list_failures(db: AsyncSession, *, limit: int) -> list[OutboxFailure]:
    rows = list(
        await db.scalars(
            select(AuthzOutbox)
            .where(AuthzOutbox.processed_at.is_(None), AuthzOutbox.attempts >= 1)
            .order_by(AuthzOutbox.attempts.desc(), AuthzOutbox.seq)
            .limit(min(limit, MAX_LIMIT))
        )
    )
    tenants = await _tenants(db, rows)
    return [_view(row, tenants.get(row.source_id)) for row in rows]


async def retry_failure(db: AsyncSession, outbox_id: UUID) -> OutboxFailure | None:
    """Make one pending row due now (caller audits and commits); ``None`` if not pending."""
    now = datetime.now(UTC)
    changed = await db.execute(
        update(AuthzOutbox)
        .where(AuthzOutbox.id == outbox_id, AuthzOutbox.processed_at.is_(None))
        .values(next_attempt_at=now)
        .returning(AuthzOutbox.id)
    )
    if changed.scalar_one_or_none() is None:
        return None
    row = await db.get(AuthzOutbox, outbox_id, populate_existing=True)
    if row is None:
        return None
    tenants = await _tenants(db, [row])
    return _view(row, tenants.get(row.source_id))
