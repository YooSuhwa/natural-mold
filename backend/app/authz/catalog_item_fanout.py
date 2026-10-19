"""One catalog item changed -> its fanout in every organization (E-03, E-07).

Hiding a model (``is_visible`` false), deleting it, or making a system tool / MCP server
unavailable must revoke that item's fanout in every organization at once; showing or
creating it again grants it back wherever the company and organization settings allow
(``catalog_fanout.fanout_for``). Deleting a system tool, skill or MCP server through
``resource_lifecycle.on_resource_deleted`` already retires every grant on the row.

A company-level change (company model or shared credential, its organization designation,
the company catalog policy) re-derives only that company's organizations (``tenant_id``).

Lock order matches the catalog PUT and the backfill (organization rows, then objects):
every organization row is locked in id order before any object; company settings are
read without a lock (company routes already hold the company row). Nothing is written in
legacy mode.
"""

from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.catalog_fanout import (
    FGA_TYPES,
    CompanyState,
    FanoutKind,
    company_state,
    fanout_for,
    sync_fanout,
)
from app.authz.grants import GrantService
from app.authz.org_context import OrgContext
from app.authz.resource_lifecycle import LifecycleChange, apply_lifecycle
from app.authz.runtime import get_authz
from app.models.organization import Organization


async def sync_item_fanout(
    db: AsyncSession,
    kind: FanoutKind | None,
    item_id: UUID | None,
    actor_id: UUID,
    *,
    tenant_id: UUID | None = None,
    service: GrantService | None = None,
) -> LifecycleChange:
    """Re-derive one item's fanout (``kind=None``: every item) in every organization, or
    only in ``tenant_id``'s organizations (call after the change is flushed, before
    commit; ``apply_lifecycle`` the result after commit)."""
    await db.flush()
    query = select(Organization).order_by(Organization.id)
    if tenant_id is not None:
        query = query.where(Organization.tenant_id == tenant_id)
    organizations = list(
        await db.scalars(query.with_for_update().execution_options(populate_existing=True))
    )
    companies: dict[UUID, CompanyState] = {}
    only = None
    if kind is not None and item_id is not None:
        only = frozenset({(FGA_TYPES[kind], item_id)})
    outbox: list[UUID] = []
    for organization in organizations:
        if organization.tenant_id not in companies:
            companies[organization.tenant_id] = await company_state(
                db, organization.tenant_id, lock=False
            )
        want = fanout_for(companies[organization.tenant_id], organization)
        ctx = OrgContext(org_id=organization.id, tenant_id=organization.tenant_id, user_id=actor_id)
        change = await sync_fanout(db, ctx, want, only=only, service=service)
        outbox += change.outbox_ids
    return LifecycleChange(tuple(outbox))


async def commit_item_change(
    db: AsyncSession,
    request: Request,
    actor_id: UUID,
    kind: FanoutKind | None,
    item_id: UUID | None,
    *,
    tenant_id: UUID | None = None,
) -> None:
    """Route helper replacing the final ``commit``: sync (outside legacy), commit, apply."""
    authz = await get_authz(request)
    change = LifecycleChange()
    if authz.mode != "legacy":
        change = await sync_item_fanout(db, kind, item_id, actor_id, tenant_id=tenant_id)
    await db.commit()
    await apply_lifecycle(change, authz)  # 8.6 item 2: before responding
