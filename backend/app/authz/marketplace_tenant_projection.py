"""``tenant_publication`` change -> the item's company tuple (plan 7.2, 10.2; E-09).

Only ``approved`` projects ``tenant:T#member installer marketplace_item:I`` (``tuples_for``).
A change queues the before/after difference in the caller's transaction; apply it after
commit with ``apply_lifecycle``. Legacy queues nothing.

No revocation marker is recorded when the company tuple is deleted: a company-public
decision is only reachable while the row says ``approved`` (``marketplace_access.item_org``
and the candidate SQL), so the committed DB row already closes other organizations'
access before the OpenFGA delete lands, and the item's own organization is not affected.
"""

from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import tuple_rows as rows
from app.authz.outbox import TupleOp, enqueue
from app.authz.resource_lifecycle import LifecycleChange, source_row
from app.authz.service_types import AuthzMode
from app.authz.tuples import tuples_for
from app.models.marketplace import MarketplaceItem


async def tenant_publication_changed(
    db: AsyncSession, item: MarketplaceItem, previous: str, *, mode: AuthzMode
) -> LifecycleChange:
    """Call after assigning the new ``tenant_publication``."""
    if mode == "legacy":
        return LifecycleChange()
    after = source_row(item)
    if not isinstance(after, rows.MarketplaceItemRow):
        raise TypeError("not a marketplace item row")
    before = replace(after, tenant_publication=previous)
    now = datetime.now(UTC)
    old, new = set(tuples_for(before, now=now)), set(tuples_for(after, now=now))
    ops = [TupleOp("delete", t) for t in sorted(old - new, key=lambda t: t.sort_key)]
    ops += [TupleOp("write", t) for t in sorted(new - old, key=lambda t: t.sort_key)]
    if not ops:
        return LifecycleChange()
    ids = await enqueue(db, ops, source_table=item.__tablename__, source_id=item.id, now=now)
    return LifecycleChange(tuple(ids))
