"""8.6 item 3, step 3: close revocations once their outbox rows all landed.

Rows sourced from ``resource_grants`` close a revoking grant. Rows sourced from
``authz_revocations`` belong to an in-place narrowing of an active grant (shorter expiry,
fewer run sources, any condition change): that marker finalizes when its rewrite landed.

A revoking grant whose outbox rows all landed becomes ``revoked``.

Its ``scope=grant`` marker (subject = the principal's FGA subject, object = the resource)
is finalized only when no other grant on the same principal/object pair is still
``revoking``: markers carry no relation, so the last revocation of the pair closes it and
the deny overlay stays in force until every delete of the pair is confirmed.
"""

from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.revocations import finalize_revocations
from app.authz.tuples import principal_ref
from app.models.authz import AuthzOutbox, AuthzRevocation, ResourceGrant

GRANTS_TABLE = "resource_grants"
MARKERS_TABLE = "authz_revocations"


def marker_refs(grant: ResourceGrant) -> tuple[str, str]:
    """(subject, object) of the grant's revocation marker."""
    subject = principal_ref(grant.principal_type, grant.principal_id, grant.relation)
    return subject, f"{grant.resource_type}:{grant.resource_id}"


async def complete_sources(
    db: AsyncSession, sources: Collection[tuple[str, UUID]], *, now: datetime
) -> None:
    """Completion for the (source_table, source_id) pairs of rows just applied."""
    await complete_revocations(db, [i for table, i in sources if table == GRANTS_TABLE], now=now)
    markers = [i for table, i in sources if table == MARKERS_TABLE]
    if markers:
        await finalize_revocations(db, await _landed(db, MARKERS_TABLE, markers), now=now)


def _pending_sources(table: str) -> Select[tuple[UUID]]:
    return select(AuthzOutbox.source_id).where(
        AuthzOutbox.source_table == table, AuthzOutbox.processed_at.is_(None)
    )


async def _landed(db: AsyncSession, table: str, ids: Collection[UUID]) -> list[UUID]:
    pending = await db.scalars(_pending_sources(table).where(AuthzOutbox.source_id.in_(list(ids))))
    return sorted(set(ids) - set(pending))


async def complete_revocations(
    db: AsyncSession, grant_ids: Collection[UUID], *, now: datetime
) -> list[UUID]:
    """Close finished revocations inside the caller's transaction; returns revoked ids."""
    if not grant_ids:
        return []
    landed = await _landed(db, GRANTS_TABLE, grant_ids)
    finished = list(
        await db.scalars(
            select(ResourceGrant)
            .where(ResourceGrant.id.in_(landed), ResourceGrant.state == "revoking")
            .order_by(ResourceGrant.id)
            .with_for_update()
        )
    )
    for grant in finished:
        grant.state = "revoked"
        grant.revoked_at = now
    await db.flush()
    for grant in finished:
        await _finalize_pair(db, grant, now)
    return [grant.id for grant in finished]


async def _finalize_pair(db: AsyncSession, grant: ResourceGrant, now: datetime) -> None:
    still_revoking = await db.scalar(
        select(ResourceGrant.id)
        .where(
            ResourceGrant.org_id == grant.org_id,
            ResourceGrant.resource_type == grant.resource_type,
            ResourceGrant.resource_id == grant.resource_id,
            ResourceGrant.principal_type == grant.principal_type,
            ResourceGrant.principal_id == grant.principal_id,
            ResourceGrant.state == "revoking",
        )
        .limit(1)
    )
    if still_revoking is not None:
        return
    subject, obj = marker_refs(grant)
    markers = await db.scalars(
        select(AuthzRevocation.id).where(
            AuthzRevocation.org_id == grant.org_id,
            AuthzRevocation.scope == "grant",
            AuthzRevocation.subject == subject,
            AuthzRevocation.object == obj,
            AuthzRevocation.finalized_at.is_(None),
            AuthzRevocation.id.not_in(_pending_sources(MARKERS_TABLE)),  # narrowing in flight
        )
    )
    await finalize_revocations(db, list(markers), now=now)
