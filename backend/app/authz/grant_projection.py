"""Outbox operations for one grant change, recomputed per tuple key (plan 8.6, C-02 merge).

OpenFGA stores one tuple per (user, relation, object) while several rows may project the
same key (an organization fanout's ``tenant`` tuple on a marketplace item). A change is the
per-key difference between the merged projection of all rows before and after it:
a key nothing projects any more is deleted, a key whose merged tuple changed is rewritten
(``replace``), and a key another active row still projects identically is left alone.
"""

from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import tuple_rows as rows
from app.authz.outbox import TupleOp
from app.authz.tuple_loaders import active_grant_rows, marketplace_item_row
from app.authz.tuple_merge import merge_tuples
from app.authz.tuple_types import RelationTuple, TupleRuleError
from app.authz.tuples import tuples_for
from app.models.authz import ResourceGrant
from app.models.marketplace import MarketplaceItem

Key = tuple[str, str, str]


async def lock_object(db: AsyncSession, resource_type: str, resource_id: UUID) -> None:
    """Serialize changes of one object before reading its remaining rows (8.6).

    Order (after any organization row lock): the marketplace item row, then every live
    grant on the object by id. Two revocations of grants sharing a key then see each
    other's ``revoking`` state, so the last one always deletes the shared tuple.
    """
    if resource_type == "marketplace_item":
        await db.scalar(
            select(MarketplaceItem.id).where(MarketplaceItem.id == resource_id).with_for_update()
        )
    await db.scalars(
        select(ResourceGrant)
        .where(
            ResourceGrant.resource_type == resource_type,
            ResourceGrant.resource_id == resource_id,
            ResourceGrant.state.in_(("active", "revoking")),
        )
        .order_by(ResourceGrant.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _key(item: RelationTuple) -> Key:
    return (item.user, item.relation, item.object)


def written_tuples(row: rows.GrantRow) -> tuple[RelationTuple, ...]:
    """What an active grant projected when written, independent of today's clock.

    A row no rule can project (corrupt legacy data) was never written, so it has none.
    """
    try:
        return tuples_for(replace(row, state="active"), now=row.granted_at)
    except TupleRuleError:
        return ()


def _safe(row: rows.Row, now: datetime) -> tuple[RelationTuple, ...]:
    # Another row's bad data must never block a revocation; reconcile reports it.
    try:
        return tuples_for(row, now=now)
    except TupleRuleError:
        return ()


async def _remaining(db: AsyncSession, row: rows.GrantRow, now: datetime) -> list[RelationTuple]:
    """Tuples every other source row projects on the grant's object.

    Cross-row admission (``tuple_filters``) is not reapplied: only organization fanout
    grants share keys today and organization principals are always admitted.
    """
    others: list[rows.Row] = [
        *await active_grant_rows(db, row.resource_type, row.resource_id, exclude=row.id)
    ]
    if row.resource_type == "marketplace_item":
        item = await marketplace_item_row(db, row.resource_id)
        if item is not None:
            others.append(item)
    return [item for other in others for item in _safe(other, now)]


def _diff(
    before: Iterable[RelationTuple], after: Iterable[RelationTuple], keys: set[Key]
) -> list[TupleOp]:
    old = {_key(item): item for item in merge_tuples(t for t in before if _key(t) in keys)}
    new = {_key(item): item for item in merge_tuples(t for t in after if _key(t) in keys)}
    ops: list[TupleOp] = []
    for key in sorted(keys):
        previous, desired = old.get(key), new.get(key)
        if desired is None and previous is not None:
            ops.append(TupleOp("delete", previous))
        elif desired is not None and desired != previous:
            ops.append(TupleOp("write", desired, replace=previous is not None))
    return ops


async def change_ops(
    db: AsyncSession,
    *,
    before: rows.GrantRow | None,
    after: rows.GrantRow | None,
    now: datetime,
) -> list[TupleOp]:
    """Ops turning the stored projection of ``before`` into ``after`` (same grant id)."""
    row = after or before
    if row is None:
        return []
    old = written_tuples(before) if before is not None else ()
    new = tuples_for(after, now=now) if after is not None else ()
    keys = {_key(item) for item in (*old, *new)}
    rest = [item for item in await _remaining(db, row, now) if _key(item) in keys]
    return _diff([*rest, *old], [*rest, *new], keys)
