"""One catalog item changed at platform level -> its fanout in every organization (E-03).

Hiding a model (``is_visible`` false), deleting it, or making a system tool / MCP server
unavailable must revoke that item's fanout in every organization at once; showing or
creating it again grants it back wherever the company and organization policies allow
(``catalog_fanout.enabled_entries``). Deleting a system tool, skill or MCP server through
``resource_lifecycle.on_resource_deleted`` already retires every grant on the row.

Lock order matches the catalog PUT and the backfill (organization rows, then objects):
every organization row is locked in id order before any object; company settings are
read without a lock (a company policy edit re-syncs its own organizations, B-11/E-07).
Nothing is written in legacy mode.
"""

from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.catalog_fanout import (
    FGA_TYPES,
    enabled_entries,
    expected_fanout,
    stored_policy,
    sync_fanout,
)
from app.authz.grants import GrantService
from app.authz.org_catalog_queries import CatalogEntry, catalog_entries, company_catalog_settings
from app.authz.org_context import OrgContext
from app.authz.resource_lifecycle import LifecycleChange, apply_lifecycle
from app.authz.runtime import get_authz
from app.models.organization import Organization
from app.schemas.org_catalog import CatalogKind, CatalogPolicy


async def sync_item_fanout(
    db: AsyncSession,
    kind: CatalogKind,
    item_id: UUID,
    actor_id: UUID,
    *,
    service: GrantService | None = None,
) -> LifecycleChange:
    """Re-derive one item's fanout in every organization (call after the change is
    flushed, before commit; ``apply_lifecycle`` the result after commit)."""
    await db.flush()
    organizations = list(
        await db.scalars(
            select(Organization)
            .order_by(Organization.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    companies: dict[UUID, tuple[CatalogPolicy, tuple[CatalogEntry, ...]]] = {}
    only = frozenset({(FGA_TYPES[kind], item_id)})
    outbox: list[UUID] = []
    for organization in organizations:
        if organization.tenant_id not in companies:
            company = await company_catalog_settings(db, organization.tenant_id, lock=False)
            entries = await catalog_entries(db, company.id)
            item = tuple(e for e in entries if (e.kind, e.id) == (kind, item_id))
            companies[organization.tenant_id] = (stored_policy(company.settings), item)
        policy, item = companies[organization.tenant_id]
        want = expected_fanout(enabled_entries(item, policy, stored_policy(organization.settings)))
        ctx = OrgContext(org_id=organization.id, tenant_id=organization.tenant_id, user_id=actor_id)
        change = await sync_fanout(db, ctx, want, only=only, service=service)
        outbox += change.outbox_ids
    return LifecycleChange(tuple(outbox))


async def commit_item_change(
    db: AsyncSession, request: Request, actor_id: UUID, kind: CatalogKind, item_id: UUID
) -> None:
    """Route helper replacing the final ``commit``: sync (outside legacy), commit, apply."""
    authz = await get_authz(request)
    change = LifecycleChange()
    if authz.mode != "legacy":
        change = await sync_item_fanout(db, kind, item_id, actor_id)
    await db.commit()
    await apply_lifecycle(change, authz)  # 8.6 item 2: before responding
