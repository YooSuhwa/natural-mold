"""Detach other companies from a company being purged (I-07).

Another company's installation of the purged company's marketplace item keeps its local
copy: ``item_id``/``version_id`` are cleared and ``install_status`` becomes
``source_removed``, audited once per installing organization
(``marketplace.installation_source_removed``, in the installer's company). Every other
nullable reference from outside rows is cleared by ``detach_outside``; a non-nullable
one makes ``blocking_references`` name it so the purge refuses before any change.
"""

from collections import defaultdict
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.models.marketplace import MarketplaceInstallation
from app.services import audit_service
from app.services.platform.tenant_data_scope import (
    CompanyTable,
    cycle_breaks,
    inbound_references,
)

_INSTALL = MarketplaceInstallation


async def blocking_references(
    db: AsyncSession, tenant_id: UUID, tables: list[CompanyTable]
) -> list[str]:
    """``table.column=count`` for each non-nullable outside reference to a company row."""
    found: list[str] = []
    for ref in inbound_references(tenant_id, tables):
        if ref.nullable:
            continue
        count = await db.scalar(select(func.count()).select_from(ref.table).where(ref.where))
        if count:
            found.append(f"{ref.table.name}.{ref.column.name}={count}")
    return found


async def detach_foreign_installations(
    db: AsyncSession, tenant_id: UUID, tables: list[CompanyTable]
) -> int:
    known = {item.table.name: item for item in tables}
    items, versions = known["marketplace_items"], known["marketplace_versions"]
    source = or_(
        _INSTALL.item_id.in_(select(items.table.c.id).where(items.where)),
        _INSTALL.version_id.in_(select(versions.table.c.id).where(versions.where)),
    )
    where = and_(_INSTALL.tenant_id != tenant_id, source)
    rows = (
        await db.execute(
            select(_INSTALL.id, _INSTALL.tenant_id, _INSTALL.org_id, _INSTALL.item_id).where(where)
        )
    ).all()
    if not rows:
        return 0
    await db.execute(
        update(_INSTALL)
        .where(_INSTALL.id.in_([row.id for row in rows]))
        .values(item_id=None, version_id=None, install_status="source_removed")
        .execution_options(synchronize_session=False)
    )
    by_org: dict[tuple[UUID, UUID], list[tuple[str, str]]] = defaultdict(list)
    for row in rows:
        by_org[(row.tenant_id, row.org_id)].append((str(row.id), str(row.item_id)))
    for (installer_tenant, org_id), entries in sorted(by_org.items(), key=str):
        await audit_service.record_event(
            db,
            actor_type="system",
            action=audit_actions.MARKETPLACE_SOURCE_REMOVED,
            target_type="organization",
            target_id=org_id,
            outcome="success",
            reason_code="publisher_company_deleted",
            tenant_id=installer_tenant,
            org_id=org_id,
            metadata={
                "installation_ids": [entry[0] for entry in entries],
                "item_ids": sorted({entry[1] for entry in entries}),
            },
        )
    return len(rows)


async def detach_outside(db: AsyncSession, tenant_id: UUID, tables: list[CompanyTable]) -> None:
    refs = [ref for ref in inbound_references(tenant_id, tables) if ref.nullable]
    for ref in [*refs, *cycle_breaks(tables)]:
        await db.execute(update(ref.table).where(ref.where).values({ref.column.name: None}))
