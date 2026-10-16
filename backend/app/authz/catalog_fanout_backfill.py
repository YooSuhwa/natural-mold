"""Operator backfill of catalog fanout grants (E-03; ``cli catalog-fanout``).

Request paths write no fanout in legacy mode (catalog policy edits made there are stored
but never projected), and platform resources seeded later have none yet. The operator
re-runs this immediately before switching to shadow/enforce (and after seeding new system
tools, skills, MCP servers or models) for every organization: one transaction
per organization, organization row then company row locked (same order as the catalog
policy PUT). Rows are queued in the outbox; the C-05 drain (or bootstrap ``fill``) writes
them. ``actor_id`` is recorded as ``granted_by``/``revoked_by`` and the audit actor.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.authz.catalog_fanout import organization_fanout, sync_fanout
from app.authz.org_context import OrgContext
from app.authz.outbox import Sessions
from app.models.organization import Organization


@dataclass(frozen=True, slots=True)
class BackfillResult:
    organizations: int
    queued: int


async def backfill_catalog_fanout(
    sessions: Sessions,
    actor_id: UUID,
    *,
    tenant_id: UUID | None = None,
    org_id: UUID | None = None,
) -> BackfillResult:
    query = select(Organization.id).order_by(Organization.id)
    if tenant_id is not None:
        query = query.where(Organization.tenant_id == tenant_id)
    if org_id is not None:
        query = query.where(Organization.id == org_id)
    async with sessions() as db:
        targets = list(await db.scalars(query))
    queued = 0
    for target in targets:
        async with sessions() as db:
            organization = await db.scalar(
                select(Organization).where(Organization.id == target).with_for_update()
            )
            if organization is None:  # deleted meanwhile
                continue
            ctx = OrgContext(org_id=target, tenant_id=organization.tenant_id, user_id=actor_id)
            change = await sync_fanout(db, ctx, await organization_fanout(db, organization))
            await db.commit()
        queued += len(change.outbox_ids)
    return BackfillResult(len(targets), queued)
