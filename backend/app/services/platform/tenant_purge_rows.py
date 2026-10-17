"""The single row-deleting transaction of a company purge (I-07; see ``tenant_purge``).

Order inside the transaction: company row lock -> state re-check -> no active export ->
preflight (``PurgeBlocked``: nothing changed) -> cleanup targets and projected tuples ->
detach outside rows -> delete company tables children-first -> shred company keys ->
platform audit (``tenant.deleted`` replaces every event about the company) -> receipt ->
company row -> queued tuple deletes -> commit. Any failure rolls the whole purge back.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.outbox import TupleOp, enqueue
from app.authz.tuple_snapshot import load_snapshot
from app.authz.tuple_types import RelationTuple
from app.authz.tuples import expected_tuples
from app.config import settings
from app.models.audit_event import AuditEvent
from app.models.organization import Tenant, TenantMember
from app.models.tenant_export import TenantExport
from app.models.tenant_purge import TenantPurge
from app.models.user import User
from app.security.tenant_keys import destroy_tenant_keys
from app.services.platform.tenant_command import TenantCommand, lock_tenant, record
from app.services.platform.tenant_data_scope import CompanyTable, company_tables
from app.services.platform.tenant_deletion import due_passed
from app.services.platform.tenant_files import company_files, files_to_json
from app.services.platform.tenant_purge_detach import (
    blocking_references,
    detach_foreign_installations,
    detach_outside,
)

logger = logging.getLogger(__name__)
SOURCE_TABLE = "tenant_purge"


class PurgeBlockedError(Exception):
    """The purge refused before changing anything; ``reason`` is logged."""

    def __init__(self, reason: str, details: list[str] | None = None) -> None:
        super().__init__(reason)
        self.reason, self.details = reason, details or []


@dataclass(frozen=True, slots=True)
class DeletedCompany:
    rows: dict[str, int]
    tuples_queued: int
    detached: int


async def _guard(db: AsyncSession, tenant_id: UUID, now: datetime) -> Tenant | None:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        return None
    tenant = await lock_tenant(db, tenant_id)  # re-read under the lock (L9)
    if tenant.status != "deletion_scheduled" or not due_passed(tenant, now):
        return None
    active = await db.scalar(
        select(TenantExport.id)
        .where(TenantExport.tenant_id == tenant_id, TenantExport.status.in_(("queued", "running")))
        .limit(1)
    )
    if active is not None:
        raise PurgeBlockedError("export_in_progress", [str(active)])
    return tenant


async def _tuples(db: AsyncSession, tenant_id: UUID, now: datetime) -> list[RelationTuple]:
    if settings.authz_mode == "legacy":
        return []
    return list(expected_tuples(await load_snapshot(db, tenant_id=tenant_id), now=now))


async def _delete_tables(db: AsyncSession, tables: list[CompanyTable]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in reversed(tables):
        result = await db.execute(delete(item.table).where(item.where))
        if removed := int(getattr(result, "rowcount", 0) or 0):
            counts[item.table.name] = removed
    return counts


async def _finish_rows(
    db: AsyncSession, tenant: Tenant, receipt: TenantPurge, metadata: dict[str, object]
) -> None:
    await destroy_tenant_keys(db, tenant.id)  # crypto-shred, then the row goes too
    await db.execute(
        delete(AuditEvent).where(
            AuditEvent.target_type == "tenant", AuditEvent.target_id == str(tenant.id)
        )
    )
    await record(TenantCommand(db, tenant.id, None), tenant, audit_actions.TENANT_DELETED, metadata)
    db.add(receipt)
    await db.flush()
    await db.execute(delete(Tenant).where(Tenant.id == tenant.id))


async def delete_company(db: AsyncSession, tenant_id: UUID, now: datetime) -> DeletedCompany | None:
    try:
        tenant = await _guard(db, tenant_id, now)
        if tenant is None:
            await db.rollback()
            return None
        tables = company_tables(tenant_id)
        if blockers := await blocking_references(db, tenant_id, tables):
            raise PurgeBlockedError("outside_reference", blockers)
        members = select(TenantMember.user_id).where(TenantMember.tenant_id == tenant_id)
        member_ids = [str(user) for user in await db.scalars(members)]
        await db.execute(select(User.id).where(User.id.in_(members)).with_for_update())
        files = files_to_json(await company_files(db, tenant_id))
        tuples = await _tuples(db, tenant_id, now)
        detached = await detach_foreign_installations(db, tenant_id, tables)
        await detach_outside(db, tenant_id, tables)
        counts = await _delete_tables(db, tables)
        receipt = TenantPurge(
            tenant_id=tenant_id,
            slug=tenant.slug,
            status="rows_deleted",
            cleanup={"members": member_ids, "files": files},
        )
        metadata: dict[str, object] = {
            "slug": tenant.slug,
            "rows": counts,
            "tuples_queued": len(tuples),
            "detached_installations": detached,
        }
        await _finish_rows(db, tenant, receipt, metadata)
        ops = [TupleOp("delete", item) for item in tuples]
        await enqueue(db, ops, source_table=SOURCE_TABLE, source_id=tenant_id, now=now)
        await db.commit()
    except PurgeBlockedError as blocked:
        await db.rollback()
        logger.warning(
            "tenant purge refused for %s: %s %s", tenant_id, blocked.reason, blocked.details
        )
        return None
    except BaseException:
        await db.rollback()
        raise
    return DeletedCompany(counts, len(tuples), detached)
