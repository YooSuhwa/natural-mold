"""Database side of a reconcile run: partitions and the hold window (C-07, D12, 8.6).

Holds are time-based, not ``seq``-based: PostgreSQL assigns ``seq`` at INSERT, so a writer
that inserted before the run read ``max(seq)`` but committed (and was applied) during the
run would hide behind a ``seq`` cut-off. A run instead fixes ``started`` = start time minus
``HOLD_MARGIN`` (absorbs app/DB clock skew, ``created_at`` is DB time) before any read and
holds every key whose outbox row is unprocessed, or was created or processed since
``started``; markers that are pending or were finalized since ``started`` block writes.
The run's own rows (``authz_reconcile`` + its job id) never hold its other partitions.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import batched
from uuid import UUID

from sqlalchemy import and_, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.authz import AuthzOutbox, AuthzRevocation
from app.models.organization import Organization, Tenant

HOLD_MARGIN = timedelta(seconds=60)
SOURCE_TABLE = "authz_reconcile"
_KEY_CHUNK = 500


@dataclass(frozen=True, slots=True)
class Partition:
    label: str
    tenant_id: UUID | None = None  # None: platform rows, claims every unclaimed tuple
    org_id: UUID | None = None


async def partitions(
    db: AsyncSession, tenant_id: UUID | None, org_id: UUID | None
) -> list[Partition]:
    """One company/organization, or every company followed by the platform partition."""
    if org_id is not None:
        owner = await db.scalar(select(Organization.tenant_id).where(Organization.id == org_id))
        if owner is None or (tenant_id is not None and owner != tenant_id):
            raise ValueError(f"organization {org_id} not found in the requested company")
        return [Partition(f"organization:{org_id}", owner, org_id)]
    if tenant_id is not None:
        return [Partition(f"tenant:{tenant_id}", tenant_id)]
    tenants = await db.scalars(select(Tenant.id).order_by(Tenant.id))
    return [*(Partition(f"tenant:{t}", t) for t in tenants), Partition("platform")]


async def recent_keys(
    db: AsyncSession, keys: Sequence[str], started: datetime, job_id: UUID
) -> frozenset[str]:
    found: set[str] = set()
    own = and_(AuthzOutbox.source_table == SOURCE_TABLE, AuthzOutbox.source_id == job_id)
    for chunk in batched(keys, _KEY_CHUNK):
        query = select(AuthzOutbox.tuple_key).where(
            AuthzOutbox.tuple_key.in_(chunk),
            not_(own),
            or_(
                AuthzOutbox.processed_at.is_(None),
                AuthzOutbox.processed_at >= started,
                AuthzOutbox.created_at >= started,
            ),
        )
        found.update(await db.scalars(query.distinct()))
    return frozenset(found)


async def recent_markers(db: AsyncSession, started: datetime) -> list[tuple[str, str, str]]:
    query = select(AuthzRevocation.scope, AuthzRevocation.subject, AuthzRevocation.object)
    recent = or_(AuthzRevocation.finalized_at.is_(None), AuthzRevocation.finalized_at >= started)
    found = await db.execute(query.where(recent))
    return [(row[0], row[1], row[2]) for row in found.all()]
