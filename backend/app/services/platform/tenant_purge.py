"""Permanent deletion of one company whose deletion is due (plan S04, 7.2, 8.1; I-07).

``tenant_purge_rows.delete_company`` runs one transaction that holds the company row
lock from its first statement (the CLI and the leader job never purge the same company
at once; a cancel either wins before it or finds the company gone): re-check the state,
refuse while an export is active, preflight outside references (abort before any
change), detach other companies, delete every company row children-first, shred the
company keys, record ``tenant.deleted``, write the ``tenant_purges`` receipt and queue
the OpenFGA tuple deletes in ``authz_outbox`` (applied by the drain after commit).

After the commit (``finish_cleanup``, resumed from the receipt by every job run):
force-logout members left without a company (8.1), delete files and
``tenant_exports/<tenant>/``, drop cached company keys, mark the receipt ``completed``.
"""

import logging
import shutil
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from anyio.to_thread import run_sync
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.session_revocation import revoke_sessions_without_company
from app.models.tenant_purge import TenantPurge
from app.models.user import User
from app.security.tenant_keys import clear_unwrap_cache
from app.services.platform.tenant_files import export_root, files_from_json, remove_company_files
from app.services.platform.tenant_purge_rows import delete_company

logger = logging.getLogger(__name__)
Sessions = Callable[[], AbstractAsyncContextManager[AsyncSession]]
Leadership = Callable[[], Awaitable[bool]]


async def always_leader() -> bool:
    return True


@dataclass(frozen=True, slots=True)
class PurgeResult:
    tenant_id: UUID
    rows: dict[str, int]
    tuples_queued: int
    sessions_revoked: int
    detached_installations: int


async def _revoke(db: AsyncSession, user_ids: list[UUID]) -> int:
    if not user_ids:
        return 0
    await db.execute(select(User.id).where(User.id.in_(user_ids)).with_for_update())
    revoked = 0
    for user_id in sorted(user_ids, key=str):
        revoked += int(await revoke_sessions_without_company(db, user_id))
    return revoked


def _remove_tree(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


async def finish_cleanup(sessions: Sessions, tenant_id: UUID) -> int | None:
    """Post-commit cleanup of one receipt; ``None`` when there is nothing to finish."""
    async with sessions() as db:
        receipt = await db.get(TenantPurge, tenant_id)
        if receipt is None or receipt.status != "rows_deleted":
            return None
        cleanup = dict(receipt.cleanup or {})
        revoked = await _revoke(db, [UUID(raw) for raw in cleanup.get("members", [])])
        await db.commit()
    await remove_company_files(files_from_json(cleanup.get("files", {})))
    if (root := export_root(tenant_id)) is not None:
        await run_sync(_remove_tree, str(root))
    clear_unwrap_cache(tenant_id)
    async with sessions() as db:
        receipt = await db.get(TenantPurge, tenant_id)
        if receipt is not None:
            receipt.status, receipt.completed_at = "completed", datetime.now(UTC)
            receipt.cleanup = {**cleanup, "sessions_revoked": revoked}
            await db.commit()
    return revoked


async def pending_cleanups(sessions: Sessions) -> list[UUID]:
    async with sessions() as db:
        rows = await db.scalars(
            select(TenantPurge.tenant_id).where(TenantPurge.status == "rows_deleted")
        )
        return list(rows)


async def purge_tenant(
    sessions: Sessions,
    tenant_id: UUID,
    *,
    now: datetime | None = None,
    still_leader: Leadership = always_leader,
) -> PurgeResult | None:
    """Purge one due company; ``None`` when it is not due, blocked or leadership is lost."""
    moment = now or datetime.now(UTC)
    if not await still_leader():
        return None
    async with sessions() as db:
        deleted = await delete_company(db, tenant_id, moment)
    if deleted is None:
        return None
    revoked = await finish_cleanup(sessions, tenant_id) or 0
    logger.info("tenant %s purged: %s", tenant_id, deleted.rows)
    return PurgeResult(tenant_id, deleted.rows, deleted.tuples_queued, revoked, deleted.detached)
