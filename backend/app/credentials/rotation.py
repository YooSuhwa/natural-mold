"""Credential key rotation — re-encrypt rows whose ``key_id`` is stale (BE-S9).

Business logic moved out of ``app.scheduler``; the scheduler keeps a
same-named wrapper so the persisted APScheduler job reference
(``app.scheduler:rotate_credentials_to_active_key``) stays valid and the
test patch surface (``app.scheduler.async_session`` / ``_ROTATION_BATCH``)
is injected at call time.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from sqlalchemy import Select, and_, exists, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.credentials import service as credential_service
from app.credentials.tenant_encryption import TENANT_KEY_PREFIX
from app.models.credential import Credential
from app.models.organization import Tenant
from app.security import tenant_keys
from app.security.cipher import TenantKeyError
from app.security.key_provider import get_active_key_id

logger = logging.getLogger(__name__)

_LOCK_CLASS = 0x4B16  # pg_advisory_lock namespace for company key rotation
_local_locks: dict[uuid.UUID, asyncio.Lock] = {}


async def rotate_credentials_to_active_key(
    *,
    session_factory: Callable[[], AsyncSession],
    batch_size: int,
) -> int:
    """Re-encrypt every credential whose ``key_id`` differs from the active key.

    Iterates in pages of ``batch_size`` so a large backlog doesn't OOM
    a single transaction. Each row writes a ``rotate`` audit log; failures
    log+continue so a single bad row can't stall the rotation.

    Company-key (0x02, ``key_id`` ``t:…``) rows are not re-encrypted here: an
    instance key rotation only re-wraps the company data keys (per company,
    isolated). When ``TENANT_DATA_KEYS_ENABLED`` is on, company-scoped 0x01 rows
    are migrated to their company key and each company left without 0x01 rows
    gets ``legacy_01_closed_at``.
    """

    active_key_id = get_active_key_id()
    await tenant_keys.rewrap_all_tenants(session_factory)
    stale = Credential.key_id != active_key_id
    if settings.tenant_data_keys_enabled:
        stale = or_(stale, and_(Credential.tenant_id.is_not(None), Credential.is_system.is_(False)))
    rotated = await _re_encrypt_matching(
        select(Credential).where(~Credential.key_id.startswith(TENANT_KEY_PREFIX), stale),
        session_factory=session_factory,
        batch_size=batch_size,
    )
    if settings.tenant_data_keys_enabled:
        async with session_factory() as db:
            tenant_ids = list(await db.scalars(select(Tenant.id)))
        for tenant_id in tenant_ids:
            await _close_legacy_01(session_factory, tenant_id)
    return rotated


async def rotate_tenant_data_key(
    *,
    session_factory: Callable[[], AsyncSession],
    tenant_id: uuid.UUID,
    batch_size: int,
) -> str:
    """Rotate a company's data key and re-encrypt all of its credential rows to it.

    Serialized per company (PostgreSQL advisory lock, in-process lock elsewhere).
    Rows are sealed to the new key itself, and the pass stops if another rotation
    supersedes it, so concurrent rotations always terminate. The previous key is
    retired (still readable for rows that fail). Returns the new company key id.
    """

    if not settings.tenant_data_keys_enabled:
        raise TenantKeyError("TENANT_DATA_KEYS_ENABLED is off")
    async with _company_rotation_lock(session_factory, tenant_id):
        async with session_factory() as db:
            new_key = await tenant_keys.rotate_key(db, tenant_id)
            await db.commit()
        await re_encrypt_tenant_rows(
            session_factory=session_factory, tenant_id=tenant_id, key=new_key, batch_size=batch_size
        )
    await _close_legacy_01(session_factory, tenant_id)
    return new_key.key_id


async def re_encrypt_tenant_rows(
    *,
    session_factory: Callable[[], AsyncSession],
    tenant_id: uuid.UUID,
    key: tenant_keys.TenantDataKey,
    batch_size: int,
) -> int:
    """Seal every company row not on ``key`` to it; stops once ``key`` is no longer active."""

    async def _still_active(db: AsyncSession) -> bool:
        state = await db.scalar(select(Tenant.data_keys).where(Tenant.id == tenant_id))
        return isinstance(state, dict) and state.get("active_key_id") == key.key_id

    return await _re_encrypt_matching(
        select(Credential).where(
            Credential.tenant_id == tenant_id,
            Credential.is_system.is_(False),
            Credential.key_id != TENANT_KEY_PREFIX + key.key_id,
        ),
        session_factory=session_factory,
        batch_size=batch_size,
        key=key,
        still_current=_still_active,
    )


@asynccontextmanager
async def _company_rotation_lock(
    session_factory: Callable[[], AsyncSession], tenant_id: uuid.UUID
) -> AsyncIterator[None]:
    lock = _local_locks.setdefault(tenant_id, asyncio.Lock())
    async with lock, session_factory() as db:
        conn = await db.connection()
        pg = conn.dialect.name == "postgresql"
        params = {"c": _LOCK_CLASS, "o": int.from_bytes(tenant_id.bytes[:4], "big", signed=True)}
        if pg:
            await db.execute(text("SELECT pg_advisory_lock(:c, :o)"), params)
        try:
            yield
        finally:
            if pg:
                await db.execute(text("SELECT pg_advisory_unlock(:c, :o)"), params)
    if not lock.locked():
        _local_locks.pop(tenant_id, None)


async def _close_legacy_01(
    session_factory: Callable[[], AsyncSession], tenant_id: uuid.UUID
) -> None:
    async def _has_legacy_rows(db: AsyncSession) -> bool:
        return bool(
            await db.scalar(
                select(
                    exists().where(
                        Credential.tenant_id == tenant_id,
                        Credential.is_system.is_(False),
                        ~Credential.key_id.startswith(TENANT_KEY_PREFIX),
                    )
                )
            )
        )

    try:
        async with session_factory() as db:
            await tenant_keys.close_legacy_01(db, tenant_id, _has_legacy_rows)
            await db.commit()
    except Exception:  # noqa: BLE001 — isolate one company's failure
        logger.exception("closing format 0x01 failed for tenant %s", tenant_id)


async def _re_encrypt_matching(
    base: Select[tuple[Credential]],
    *,
    session_factory: Callable[[], AsyncSession],
    batch_size: int,
    key: tenant_keys.TenantDataKey | None = None,
    still_current: Callable[[AsyncSession], Awaitable[bool]] | None = None,
) -> int:
    rotated = 0
    # Rows that failed re-encryption keep their stale key_id, so without an
    # exclusion a batch of >=batch_size persistent failures would be
    # re-fetched forever (the len(rows) < batch guard never trips). Excluding
    # failed ids guarantees progress: every iteration either rotates a row
    # (drops out via key_id) or marks it failed (drops out via notin_).
    failed_ids: set[uuid.UUID] = set()

    while True:
        async with session_factory() as db:
            if still_current is not None and not await still_current(db):
                break
            stmt = base
            if failed_ids:
                stmt = stmt.where(Credential.id.notin_(failed_ids))
            result = await db.execute(stmt.limit(batch_size))
            rows = list(result.scalars().all())
            if not rows:
                break
            for cred in rows:
                try:
                    await credential_service.re_encrypt_with_active_key(db, cred, key=key)
                    rotated += 1
                except Exception:  # noqa: BLE001 — keep rotation moving
                    failed_ids.add(cred.id)
                    logger.exception("credential %s rotation failed; will retry next run", cred.id)
            await db.commit()
        if len(rows) < batch_size:
            break
    if failed_ids:
        logger.warning(
            "credential rotation finished with %d failed rows; they will be retried next run",
            len(failed_ids),
        )
    return rotated
